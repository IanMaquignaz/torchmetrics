# Copyright The Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from collections.abc import Sequence
from typing import Any, Optional, Union

import torch
from torch import Tensor
from typing_extensions import Literal

from torchmetrics.functional.segmentation.mean_iou import _mean_iou_compute, _mean_iou_update, _mean_iou_validate_args
from torchmetrics.metric import Metric
from torchmetrics.utilities.imports import _MATPLOTLIB_AVAILABLE
from torchmetrics.utilities.plot import _AX_TYPE, _PLOT_OUT_TYPE

if not _MATPLOTLIB_AVAILABLE:
    __doctest_skip__ = ["MeanIoU.plot"]


class MeanIoU(Metric):
    """Computes Mean Intersection over Union (mIoU) for semantic segmentation.

    The metric is defined by the overlap between the predicted segmentation and the ground truth, divided by the
    total area covered by the union of the two. The metric can be computed for each class separately or for all
    classes at once. The metric is optimal at a value of 1 and worst at a value of 0, -1 is returned if class
    is completely absent both from prediction and the ground truth labels.

    As input to ``forward`` and ``update`` the metric accepts the following input:

        - ``preds`` (:class:`~torch.Tensor`): An one-hot boolean tensor of shape ``(N, C, ...)`` with ``N`` being
          the number of samples and ``C`` the number of classes. Alternatively, an integer tensor of shape ``(N, ...)``
          can be provided, where the integer values correspond to the class index. The input type can be controlled
          with the ``input_format`` argument.
        - ``target`` (:class:`~torch.Tensor`): An one-hot boolean tensor of shape ``(N, C, ...)`` with ``N`` being
          the number of samples and ``C`` the number of classes. Alternatively, an integer tensor of shape ``(N, ...)``
          can be provided, where the integer values correspond to the class index. The input type can be controlled
          with the ``input_format`` argument.

    As output to ``forward`` and ``compute`` the metric returns the following output:

        - ``miou`` (:class:`~torch.Tensor`): The mean Intersection over Union (mIoU) score. If ``per_class`` is set to
          ``True``, the output will be a tensor of shape ``(C,)`` with the IoU score for each class. If ``per_class`` is
          set to ``False``, the output will be a scalar tensor.

    Args:
        num_classes: The number of classes in the segmentation problem. Required when input_format="index" or "mixed",
            optional when input_format="one-hot".
        include_background: Whether to include the background class in the computation
        per_class: Whether to compute the IoU for each class separately. If set to ``False``, the metric will
            compute the mean IoU over all classes.
        input_format: What kind of input the function receives.
            Choose between ``"one-hot"`` for one-hot encoded tensors, ``"index"`` for index tensors
            or ``"mixed"`` for one one-hot encoded and one index tensor
        kwargs: Additional keyword arguments, see :ref:`Metric kwargs` for more info.

    Raises:
        ValueError:
            If ``num_classes`` is not ``None`` or a positive integer
        ValueError:
            If ``num_classes`` is not provided when ``input_format`` is ``"index"`` or ``"mixed"``
        ValueError:
            If ``include_background`` is not a boolean
        ValueError:
            If ``per_class`` is not a boolean
        ValueError:
            If ``input_format`` is not one of ``"one-hot"``, ``"index"`` or ``"mixed"``

    Example:
        >>> import torch
        >>> from torch import randint
        >>> from torchmetrics.segmentation import MeanIoU
        >>> miou = MeanIoU()
        >>> preds = randint(0, 2, (10, 3, 128, 128), generator=torch.Generator().manual_seed(42))
        >>> target = randint(0, 2, (10, 3, 128, 128), generator=torch.Generator().manual_seed(43))
        >>> miou(preds, target)
        tensor(0.3336)
        >>> miou = MeanIoU(num_classes=3, per_class=True)
        >>> miou(preds, target)
        tensor([0.3361, 0.3340, 0.3308])
        >>> miou = MeanIoU(per_class=True, include_background=False)
        >>> miou(preds, target)
        tensor([0.3340, 0.3308])
        >>> miou = MeanIoU(num_classes=3, per_class=True, include_background=True, input_format="index")
        >>> miou(preds, target)
        tensor([ 0.3334,  0.3336, -1.0000])

    """

    score: Tensor
    num_batches: Tensor
    full_state_update: bool = False
    is_differentiable: bool = False
    higher_is_better: bool = True
    plot_lower_bound: float = 0.0
    plot_upper_bound: float = 1.0

    def __init__(
        self,
        num_classes: Optional[int] = None,
        include_background: bool = True,
        per_class: bool = False,
        input_format: Literal["one-hot", "index", "mixed"] = "one-hot",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        _mean_iou_validate_args(num_classes, include_background, per_class, input_format)
        self.num_classes = num_classes
        self.include_background = include_background
        self.per_class = per_class
        self.input_format = input_format
        self._is_initialized = False
        if num_classes is not None:
            num_classes = num_classes - 1 if not include_background else num_classes
            self.add_state("score", default=torch.zeros(num_classes if per_class else 1), dist_reduce_fx="sum")
            self.add_state("num_batches", default=torch.zeros(num_classes), dist_reduce_fx="sum")
            self._is_initialized = True
        else:
            self.add_state("score", default=torch.zeros(1), dist_reduce_fx="sum")
            self.add_state("num_batches", default=torch.zeros(1), dist_reduce_fx="sum")

    def update(self, preds: Tensor, target: Tensor) -> None:
        """Update the state with the new data."""
        if not self._is_initialized:
            try:
                self.num_classes = preds.shape[1]
            except IndexError as err:
                raise IndexError(f"Cannot determine `num_classes` from `preds` tensor: {preds}.") from err

            if self.num_classes == 0:
                raise ValueError(
                    f"Expected argument `num_classes` to be a positive integer, but got {self.num_classes}."
                )

            num_out_classes = self.num_classes - 1 if not self.include_background else self.num_classes
            self.add_state(
                "score",
                default=torch.zeros(num_out_classes, device=self.device, dtype=self.dtype),
                dist_reduce_fx="sum",
            )
            self.add_state(
                "num_batches",
                default=torch.zeros(num_out_classes, device=self.device, dtype=torch.int32),
                dist_reduce_fx="sum",
            )
            self._is_initialized = True

        intersection, union = _mean_iou_update(
            preds, target, self.num_classes, self.include_background, self.input_format
        )
        score = _mean_iou_compute(intersection, union, zero_division=0.0)
        # only update for classes that are present (i.e. union > 0)
        valid_classes = union > 0
        if self.per_class:
            self.score += (score * valid_classes).sum(dim=0)
            self.num_batches += valid_classes.sum(dim=0)
        else:
            self.score += (score * valid_classes).sum()
            self.num_batches += valid_classes.sum()

    def compute(self) -> Tensor:
        """Compute the final Mean Intersection over Union (mIoU)."""
        output_score = self.score / self.num_batches
        return output_score.nan_to_num(-1.0) if self.per_class else output_score.nanmean()

    def plot(self, val: Union[Tensor, Sequence[Tensor], None] = None, ax: Optional[_AX_TYPE] = None) -> _PLOT_OUT_TYPE:
        """Plot a single or multiple values from the metric.

        Args:
            val: Either a single result from calling `metric.forward` or `metric.compute` or a list of these results.
                If no value is provided, will automatically call `metric.compute` and plot that result.
            ax: An matplotlib axis object. If provided will add plot to that axis

        Returns:
            Figure and Axes object

        Raises:
            ModuleNotFoundError:
                If `matplotlib` is not installed

        .. plot::
            :scale: 75

            >>> # Example plotting a single value
            >>> import torch
            >>> from torchmetrics.audio import PerceptualEvaluationSpeechQuality
            >>> metric = PerceptualEvaluationSpeechQuality(8000, 'nb')
            >>> metric.update(torch.rand(8000), torch.rand(8000))
            >>> fig_, ax_ = metric.plot()

        .. plot::
            :scale: 75

            >>> # Example plotting multiple values
            >>> import torch
            >>> from torchmetrics.audio import PerceptualEvaluationSpeechQuality
            >>> metric = PerceptualEvaluationSpeechQuality(8000, 'nb')
            >>> values = [ ]
            >>> for _ in range(10):
            ...     values.append(metric(torch.rand(8000), torch.rand(8000)))
            >>> fig_, ax_ = metric.plot(values)

        """
        return self._plot(val, ax)

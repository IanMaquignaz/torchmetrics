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
from typing import Callable, Optional, Union

import numpy as np
import pytest
import torch
from sklearn.metrics import label_ranking_average_precision_score
from torch import Tensor
from typing_extensions import Literal

from torchmetrics.functional.retrieval.reciprocal_rank import retrieval_reciprocal_rank
from torchmetrics.retrieval.reciprocal_rank import RetrievalMRR
from unittests._helpers import seed_all
from unittests.retrieval.helpers import (
    RetrievalMetricTester,
    _concat_tests,
    _custom_aggregate_fn,
    _default_metric_class_input_arguments,
    _default_metric_class_input_arguments_ignore_index,
    _default_metric_functional_input_arguments,
    _errors_test_class_metric_parameters_default,
    _errors_test_class_metric_parameters_no_pos_target,
    _errors_test_functional_metric_parameters_default,
)

seed_all(42)


def _reciprocal_rank_at_k(target: np.ndarray, preds: np.ndarray, top_k: Optional[int] = None):
    """Adaptation of `sklearn.metrics.label_ranking_average_precision_score`.

    Since the original sklearn metric works as RR only when the number of positive targets is exactly 1, here we remove
    every positive target that is not the most important. Remember that in RR only the positive target with the highest
    score is considered.

    """
    assert target.shape == preds.shape
    assert len(target.shape) == 1  # works only with single dimension inputs

    # take k largest predictions here because sklearn does not allow it
    if top_k is not None:
        top_k = min(top_k, len(preds))
        ind = np.argpartition(preds, -top_k)[-top_k:]
        target = target[ind]
        preds = preds[ind]

    # going to remove T targets that are not ranked as highest
    indexes = preds[target.astype(bool)]
    if len(indexes) > 0:
        target[preds != indexes.max(-1, keepdims=True)[0]] = 0  # ensure that only 1 positive label is present

    if target.sum() > 0:
        # sklearn `label_ranking_average_precision_score` requires at most 2 dims
        return label_ranking_average_precision_score(np.expand_dims(target, axis=0), np.expand_dims(preds, axis=0))
    return 0.0


class TestMRR(RetrievalMetricTester):
    """Test class for `RetrievalMRR` metric."""

    @pytest.mark.parametrize("ddp", [pytest.param(True, marks=pytest.mark.DDP), False])
    @pytest.mark.parametrize("empty_target_action", ["skip", "neg", "pos"])
    @pytest.mark.parametrize("ignore_index", [None, 1])  # avoid setting 0, otherwise test with all 0 targets will fail
    @pytest.mark.parametrize("top_k", [None, 1, 4, 10])
    @pytest.mark.parametrize("aggregation", ["mean", "median", "max", "min", _custom_aggregate_fn])
    @pytest.mark.parametrize(**_default_metric_class_input_arguments)
    def test_class_metric(
        self,
        ddp: bool,
        indexes: Tensor,
        preds: Tensor,
        target: Tensor,
        empty_target_action: str,
        ignore_index: int,
        top_k: int,
        aggregation: Union[Literal["mean", "median", "min", "max"], Callable],
    ):
        """Test class implementation of metric."""
        metric_args = {
            "empty_target_action": empty_target_action,
            "ignore_index": ignore_index,
            "top_k": top_k,
            "aggregation": aggregation,
        }

        self.run_class_metric_test(
            ddp=ddp,
            indexes=indexes,
            preds=preds,
            target=target,
            metric_class=RetrievalMRR,
            reference_metric=_reciprocal_rank_at_k,
            metric_args=metric_args,
        )

    @pytest.mark.parametrize("ddp", [pytest.param(True, marks=pytest.mark.DDP), False])
    @pytest.mark.parametrize("empty_target_action", ["skip", "neg", "pos"])
    @pytest.mark.parametrize("top_k", [None, 1, 4, 10])
    @pytest.mark.parametrize(**_default_metric_class_input_arguments_ignore_index)
    def test_class_metric_ignore_index(
        self,
        ddp: bool,
        indexes: Tensor,
        preds: Tensor,
        target: Tensor,
        empty_target_action: str,
        top_k: int,
    ):
        """Test class implementation of metric with ignore_index argument."""
        metric_args = {"empty_target_action": empty_target_action, "ignore_index": -100, "top_k": top_k}

        self.run_class_metric_test(
            ddp=ddp,
            indexes=indexes,
            preds=preds,
            target=target,
            metric_class=RetrievalMRR,
            reference_metric=_reciprocal_rank_at_k,
            metric_args=metric_args,
        )

    @pytest.mark.parametrize(**_default_metric_functional_input_arguments)
    @pytest.mark.parametrize("top_k", [None, 1, 4, 10])
    def test_functional_metric(self, preds: Tensor, target: Tensor, top_k: int):
        """Test functional implementation of metric."""
        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=retrieval_reciprocal_rank,
            reference_metric=_reciprocal_rank_at_k,
            metric_args={},
            top_k=top_k,
        )

    @pytest.mark.parametrize(**_default_metric_class_input_arguments)
    def test_precision_cpu(self, indexes: Tensor, preds: Tensor, target: Tensor):
        """Test dtype support of the metric on CPU."""
        self.run_precision_test_cpu(
            indexes=indexes,
            preds=preds,
            target=target,
            metric_module=RetrievalMRR,
            metric_functional=retrieval_reciprocal_rank,
        )

    @pytest.mark.parametrize(**_default_metric_class_input_arguments)
    def test_precision_gpu(self, indexes: Tensor, preds: Tensor, target: Tensor):
        """Test dtype support of the metric on GPU."""
        self.run_precision_test_gpu(
            indexes=indexes,
            preds=preds,
            target=target,
            metric_module=RetrievalMRR,
            metric_functional=retrieval_reciprocal_rank,
        )

    @pytest.mark.parametrize(
        **_concat_tests(
            _errors_test_class_metric_parameters_default,
            _errors_test_class_metric_parameters_no_pos_target,
        )
    )
    def test_arguments_class_metric(
        self, indexes: Tensor, preds: Tensor, target: Tensor, message: str, metric_args: dict
    ):
        """Test that specific errors are raised for incorrect input."""
        self.run_metric_class_arguments_test(
            indexes=indexes,
            preds=preds,
            target=target,
            metric_class=RetrievalMRR,
            message=message,
            metric_args=metric_args,
            exception_type=ValueError,
            kwargs_update={},
        )

    @pytest.mark.parametrize(**_errors_test_functional_metric_parameters_default)
    def test_arguments_functional_metric(self, preds: Tensor, target: Tensor, message: str, metric_args: dict):
        """Test that specific errors are raised for incorrect input."""
        self.run_functional_metric_arguments_test(
            preds=preds,
            target=target,
            metric_functional=retrieval_reciprocal_rank,
            message=message,
            exception_type=ValueError,
            kwargs_update=metric_args,
        )

    def test_mrr_all_zero_predictions_returns_zero(self):
        """Test that MRR returns 0 when all predictions are zero and there are positive targets."""
        preds = torch.tensor([0.0, 0.0, 0.0])
        target = torch.tensor([1, 0, 1])
        indexes = torch.tensor([0, 0, 0])
        metric = RetrievalMRR()
        result = metric(preds, target, indexes=indexes)
        assert result == 0.0, f"Expected MRR to be 0.0, got {result}"

import numpy as np
import pandas as pd
import pytest
from numpy.testing import assert_array_equal

from efficient_probit_regression.datasets import BaseDataset, Iris
from efficient_probit_regression.experiments import (
    LeverageScoreSamplingExperiment,
    OnlineRidgeLeverageScoreSamplingExperiment,
    SGDExperiment,
    UniformSamplingExperiment,
    UniformSamplingExperimentBayes,
)


class ExampleDataset(BaseDataset):
    def __init__(self):
        super().__init__(use_caching=False)

    def get_name(self):
        return "example_name"

    def load_X_y(self):
        X = np.array([[1, 0], [0.1, 1], [-0.1, 1], [-1, 0], [0, -1]])
        y = np.array([1, -1, -1, 1, -1])
        return X, y


@pytest.mark.parametrize(
    "ExperimentClass",
    [
        UniformSamplingExperiment,
        LeverageScoreSamplingExperiment,
        OnlineRidgeLeverageScoreSamplingExperiment,
        SGDExperiment,
    ],
)
def test_experiment(tmp_path, ExperimentClass):
    dataset = ExampleDataset()
    results_filename = tmp_path / "results.csv"
    experiment = ExperimentClass(
        dataset=dataset,
        results_filename=results_filename,
        min_size=1,
        max_size=5,
        step_size=2,
        num_runs=3,
    )
    experiment.run()

    df = pd.read_csv(results_filename)

    run_unique, run_counts = np.unique(df["run"], return_counts=True)
    assert_array_equal(run_unique, [1, 2, 3])
    assert_array_equal(run_counts, [3, 3, 3])

    assert np.all(df["ratio"][~df["ratio"].isna()] >= 1)

    assert np.sum(df["sampling_time_s"].isna()) == 0
    assert np.sum(df["total_time_s"].isna()) == 0

    assert np.all(df["sampling_time_s"] > 0)
    assert np.all(df["total_time_s"] > 0)


def test_uniform_sampling_reduction(tmp_path):
    dataset = ExampleDataset()
    results_filename = tmp_path / "results.csv"
    experiment = UniformSamplingExperiment(
        dataset=dataset,
        results_filename=results_filename,
        min_size=1,
        max_size=5,
        step_size=1,
        num_runs=1,
    )

    for cur_config in experiment.get_config_grid():
        cur_X, cur_y, cur_weights = experiment.get_reduced_X_y_weights(cur_config)
        assert_array_equal(cur_weights, np.ones(cur_config["size"]))
        assert cur_X.shape[0] == cur_config["size"]
        assert cur_X.shape[1] == dataset.get_d()
        assert cur_y.shape[0] == cur_config["size"]


def test_leverage_score_sampling_experiment_parallel(tmp_path):
    dataset = ExampleDataset()
    results_filename = tmp_path / "results.csv"
    experiment = LeverageScoreSamplingExperiment(
        dataset=dataset,
        results_filename=results_filename,
        min_size=1,
        max_size=5,
        step_size=2,
        num_runs=3,
    )
    experiment.run(parallel=True)

    df = pd.read_csv(results_filename)

    run_unique, run_counts = np.unique(df["run"], return_counts=True)
    assert_array_equal(run_unique, [1, 2, 3])
    assert_array_equal(run_counts, [3, 3, 3])

    assert np.all(df["ratio"][~df["ratio"].isna()] >= 1)

    assert np.sum(df["sampling_time_s"].isna()) == 0
    assert np.sum(df["total_time_s"].isna()) == 0

    assert np.all(df["sampling_time_s"] > 0)
    assert np.all(df["total_time_s"] > 0)


def test_leverage_score_sampling_reduction(tmp_path):
    dataset = ExampleDataset()
    results_filename = tmp_path / "results.csv"
    experiment = LeverageScoreSamplingExperiment(
        dataset=dataset,
        results_filename=results_filename,
        min_size=1,
        max_size=5,
        step_size=1,
        num_runs=1,
        only_compute_once=False,
    )

    for cur_config in experiment.get_config_grid():
        cur_X, cur_y, cur_weights = experiment.get_reduced_X_y_weights(cur_config)
        assert cur_X.shape[0] == cur_config["size"]
        assert cur_y.shape[0] == cur_config["size"]
        assert cur_X.shape[1] == dataset.get_d()
        assert cur_weights.shape[0] == cur_config["size"]


def test_bayes_iris(tmp_path):
    dataset = Iris()
    experiment = UniformSamplingExperimentBayes(
        dataset=dataset,
        num_runs=3,
        min_size=50,
        max_size=100,
        step_size=25,
        prior_mean=np.zeros(dataset.get_d()),
        prior_cov=10 * np.eye(dataset.get_d()),
        samples_per_chain=100,
        num_chains=2,
    )
    experiment.run(results_dir=tmp_path)

    # assert that all files are ok
    for cur_run in [1, 2, 3]:
        cur_path = tmp_path / f"iris_sample_uniform_run_{cur_run}.csv"
        assert cur_path.exists()

        cur_df = pd.read_csv(cur_path)

        assert set(cur_df.columns) == {
            "beta_0",
            "beta_1",
            "beta_2",
            "beta_3",
            "beta_4",
            "size",
            "run",
        }
        assert cur_df.shape == (600, 7)
        assert set(cur_df["size"]) == {50, 75, 100}
        assert set(cur_df["run"]) == {cur_run}

        for cur_size in [50, 75, 100]:
            sub_df = cur_df.loc[cur_df["size"] == cur_size]
            assert sub_df.shape == (200, 7)

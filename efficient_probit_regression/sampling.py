import numba
import numpy as np
import scipy as sp
from joblib import Parallel, delayed
from scipy.stats import multivariate_normal, truncnorm

_rng = np.random.default_rng()


def _check_sample(X, y, sample_size):
    if X.shape[0] != y.shape[0]:
        raise ValueError(
            f"Incompatible shapes of X and y: {X.shape[0]} != {y.shape[0]}"
        )

    if sample_size > X.shape[0]:
        raise ValueError("Sample size can't be greater than total number of samples!")

    if sample_size <= 0:
        raise ValueError("Sample size must be greater than zero!")


def uniform_sampling(X: np.ndarray, y: np.ndarray, sample_size: int):
    """
    Draw a uniform sample of X and y without replacement.

    Returns
    -------
    X, y : Sample
    """
    _check_sample(X, y, sample_size)

    sample_indices = _rng.choice(X.shape[0], size=sample_size, replace=False)

    return X[sample_indices], y[sample_indices]


def compute_leverage_scores(X: np.ndarray):
    if not len(X.shape) == 2:
        raise ValueError("X must be 2D!")

    Q, *_ = np.linalg.qr(X)
    leverage_scores = np.linalg.norm(Q, axis=1) ** 2

    return leverage_scores


@numba.jit(nopython=True)
def _check_norm_change(Q, x):
    Q = np.ascontiguousarray(Q)
    x = np.ascontiguousarray(x)
    return np.abs(np.linalg.norm(Q.T @ x) - np.linalg.norm(x)) < 1e-6


@numba.jit(nopython=True)
def _fast_inv_update(M_inv, outer, x):
    M_inv = np.ascontiguousarray(M_inv)
    outer = np.ascontiguousarray(outer)
    x = np.ascontiguousarray(x)
    scalar = 1 + np.dot(x, M_inv @ x)
    M_inv -= 1 / scalar * (M_inv @ outer @ M_inv)


def _compute_leverage_scores_online_pinv(X: np.ndarray):
    n = X.shape[0]
    d = X.shape[1]

    M = np.zeros(shape=(d, d))
    M_inv = np.zeros(shape=(d, d))
    Q = np.zeros(shape=(d, d))

    X = X.astype(float)

    leverage_scores = []

    for i in range(n):
        cur_row = X[i]
        outer = np.outer(cur_row, cur_row)
        M += outer
        if _check_norm_change(Q, cur_row):
            _fast_inv_update(M_inv, outer, cur_row)
        else:
            M_inv = np.linalg.pinv(M)
            Q = sp.linalg.orth(M)
            r = Q.shape[1]
            if r < d:
                Q = np.concatenate((Q, np.zeros((d, d - r))), axis=1)

        cur_leverage_score = np.dot(cur_row, M_inv @ cur_row)
        cur_leverage_score = np.minimum(cur_leverage_score, 1)
        cur_leverage_score = np.maximum(cur_leverage_score, 0)
        leverage_scores.append(cur_leverage_score)

    return np.array(leverage_scores)


def _compute_leverage_scores_online_solve(X: np.ndarray):
    n = X.shape[0]
    d = X.shape[1]

    ATA = np.zeros(shape=(d, d))

    leverage_scores = []

    for i in range(n):
        cur_row = X[i]
        ATA += np.outer(cur_row, cur_row)
        try:
            cur_leverage_score = np.dot(cur_row, np.linalg.solve(ATA, cur_row))
            if cur_leverage_score < 0:
                cur_leverage_score = np.dot(
                    cur_row, np.linalg.lstsq(ATA, cur_row, rcond=None)[0]
                )
        except np.linalg.LinAlgError:
            cur_leverage_score = np.dot(
                cur_row, np.linalg.lstsq(ATA, cur_row, rcond=None)[0]
            )
        cur_leverage_score = np.minimum(cur_leverage_score, 1)
        leverage_scores.append(cur_leverage_score)

    return np.array(leverage_scores)


def compute_leverage_scores_online(X: np.ndarray, method="pinv"):
    if method == "pinv":
        return _compute_leverage_scores_online_pinv(X)
    elif method == "solve":
        return _compute_leverage_scores_online_solve(X)
    else:
        raise ValueError("Method must be either pinv or solve!")


def _round_up(x: np.ndarray) -> np.ndarray:
    """
    Rounds each element in x up to the nearest power of two.
    """
    if not np.all(x >= 0):
        raise ValueError("All elements of x must be greater than zero!")

    greater_zero = x > 0

    results = x.copy()
    results[greater_zero] = np.power(2, np.ceil(np.log2(x[greater_zero])))

    return results


def leverage_score_sampling(
    X: np.ndarray,
    y: np.ndarray,
    sample_size: int,
    augmented: bool = False,
    online: bool = False,
    round_up: bool = False,
    precomputed_scores: np.ndarray = None,
):
    """
    Draw a leverage score weighted sample of X and y without replacement.

    Parameters
    ----------
    X : np.ndarray
        Data Matrix
    y : np.ndarray
        Label vector
    sample_size : int
        Sample size
    augmented : bool
        Wether to add the additive 1 / |W| term
    online : bool
        Compute online leverage scores in one pass over the data
    round_up : bool
        Round the leverage scores up to the nearest power of two
    precomputed_scores : np.ndarray
        To avoid recomputing the leverage scores every time,
        pass the precomputed scores here.

    Returns
    -------
    X, y : Sample
    w : New sample weights
    """
    _check_sample(X, y, sample_size)

    if precomputed_scores is None:
        if online:
            leverage_scores = compute_leverage_scores_online(X)
        else:
            leverage_scores = compute_leverage_scores(X)
    else:
        leverage_scores = precomputed_scores

    if augmented:
        leverage_scores = leverage_scores + 1 / X.shape[0]

    if round_up:
        leverage_scores = _round_up(leverage_scores)

    p = leverage_scores / np.sum(leverage_scores)

    w = 1 / (p * sample_size)

    sample_indices = _rng.choice(
        X.shape[0],
        size=sample_size,
        replace=False,
        p=p,
    )

    return X[sample_indices], y[sample_indices], w[sample_indices]


@numba.jit(nopython=True)
def _fast_leverage_score(row, A):
    return np.dot(
        np.ascontiguousarray(row), np.ascontiguousarray(np.linalg.solve(A, row))
    )


def online_ridge_leverage_score_sampling(
    X: np.ndarray,
    y: np.ndarray,
    sample_size: int,
    augmentation_constant: float = None,
    lambda_ridge: float = 1e-6,
):
    """
    Sample X and y proportional to the online ridge leverage scores.
    """
    n, d = X.shape

    ATA_ridge = lambda_ridge * np.eye(d)

    sampler = ReservoirSampler(sample_size=sample_size, d=d)

    # the remaining samples
    for i in range(n):
        cur_row = X[i]
        cur_label = y[i]

        cur_ridge_leverage_score = _fast_leverage_score(cur_row, ATA_ridge)
        cur_weight = np.minimum(cur_ridge_leverage_score, 1)

        if augmentation_constant is not None:
            cur_weight += augmentation_constant

        sampler.insert_record(row=cur_row, label=cur_label, weight=cur_weight)

        if sampler.was_last_record_sampled():
            ATA_ridge += cur_row[:, np.newaxis] @ cur_row[np.newaxis, :]

    X_sample, y_sample = sampler.get_sample()
    return X_sample, y_sample, np.ones(y_sample.shape)


class ReservoirSampler:
    """
    Implementation of a reservoir sampler as described in
    "A general purpose unequal probability sampling plan" by M. T. Chao,
    adapted here for row sampling of datasets consisting of a data matrix X
    and a label vector y.

    Parameters
    ----------
    sample_size : int
        Numer of rows in the resulting sample.

    d : int
        Second dimension of the sample.
        The whole sample will have a dimension of sample_size x d.
    """

    def __init__(self, sample_size: int, d: int):
        self.sample_size = sample_size
        self.d = d
        self._sample_X = np.empty(shape=(sample_size, d))
        self._sample_y = np.empty(shape=(sample_size,))
        self._row_counter = 0
        self._weight_sum = 0
        self._last_record_sampled = False

    def get_sample(self):
        """
        Returns the sample of X and the sample of y.
        """
        if self._row_counter < self.sample_size:
            return (
                self._sample_X[: self._row_counter],
                self._sample_y[: self._row_counter],
            )
        return self._sample_X, self._sample_y

    def insert_record(self, row: np.ndarray, label: float, weight: float):
        """
        Insert a data record consisting of a row and a label.
        The record will be sampled with a probability that is proportional to
        the given weight.
        """
        self._weight_sum += weight

        if self._row_counter < self.sample_size:
            self._sample_X[self._row_counter] = row
            self._sample_y[self._row_counter] = label
            self._row_counter += 1
            self._last_record_sampled = True
            return

        p = self.sample_size * weight / self._weight_sum
        if _rng.random() < p:
            random_index = _rng.choice(self.sample_size)
            self._sample_X[random_index] = row
            self._sample_y[random_index] = label
            self._row_counter += 1
            self._last_record_sampled = True
            return

        self._last_record_sampled = False

    def was_last_record_sampled(self):
        return self._last_record_sampled


def truncated_normal(a, b, mean, std, size, random_state=None):
    """
    This is a wrapper around scipy.stats.distributions.truncnorm for
    drawing random samples from a truncated normal distribution.

    The parameters a and b specify the actual interval where the
    probability mass is located, mean and std specify the
    original normal distribution.
    """
    a_scipy = (a - mean) / std
    b_scipy = (b - mean) / std
    return truncnorm.rvs(
        a=a_scipy, b=b_scipy, loc=mean, scale=std, size=size, random_state=random_state
    )


def gibbs_sampler_probit(
    X: np.ndarray,
    y: np.ndarray,
    prior_mean: np.ndarray,
    prior_cov: np.ndarray,
    num_samples,
    num_chains,
    min_burn_in=100,
):
    prior_cov_inv = np.linalg.inv(prior_cov)
    B = np.linalg.inv(prior_cov_inv + X.T @ X)

    def draw_sample(latent):
        beta_mean = B @ (prior_cov_inv @ prior_mean + X.T @ latent)
        beta = multivariate_normal.rvs(size=1, mean=beta_mean, cov=B)

        a = np.where(y == -1, -np.inf, 0)
        b = np.where(y == -1, 0, np.inf)
        latent_mean = X @ beta
        latent = truncated_normal(
            a,
            b,
            mean=latent_mean,
            std=1,
            size=latent.shape[0],
        )

        return beta, latent

    def simulate_chain():
        latent = np.zeros(y.shape)
        burn_in = max(int(0.01 * num_samples), min_burn_in)
        for i in range(burn_in):
            beta, latent = draw_sample(latent)

        samples = []
        for i in range(num_samples):
            beta, latent = draw_sample(latent)
            samples.append(beta)

        return np.array(samples)

    if num_chains == 1:
        samples = simulate_chain()
    else:
        sample_chunks = Parallel(n_jobs=num_chains)(
            delayed(simulate_chain)() for i in range(num_chains)
        )
        samples = np.vstack(sample_chunks)

    return samples

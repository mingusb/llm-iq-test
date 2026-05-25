import math
from typing import Dict, Iterable, List, Tuple


def standardize(values: Iterable[float]) -> List[float]:
    values_list = list(values)
    if not values_list:
        raise ValueError("values must be non-empty")
    mean = sum(values_list) / len(values_list)
    variance = sum((value - mean) ** 2 for value in values_list) / len(values_list)
    std = math.sqrt(variance)
    if std == 0:
        return [0.0 for _ in values_list]
    return [(value - mean) / std for value in values_list]


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def estimate_theta_rasch(
    responses: List[bool],
    difficulties: List[float],
    *,
    prior_mean: float = 0.0,
    prior_sd: float = 1.0,
    grid_min: float = -6.0,
    grid_max: float = 6.0,
    grid_step: float = 0.01,
) -> Dict[str, float]:
    if len(responses) != len(difficulties):
        raise ValueError("responses and difficulties must have the same length")
    if not responses:
        raise ValueError("responses must be non-empty")
    if prior_sd <= 0:
        raise ValueError("prior_sd must be positive")

    best_theta = grid_min
    best_log_post = -float("inf")

    theta = grid_min
    while theta <= grid_max + 1e-12:
        log_likelihood = 0.0
        for response, difficulty in zip(responses, difficulties):
            prob = _sigmoid(theta - difficulty)
            prob = max(min(prob, 1.0 - 1e-12), 1e-12)
            log_likelihood += math.log(prob) if response else math.log(1.0 - prob)
        prior_term = -0.5 * ((theta - prior_mean) / prior_sd) ** 2
        log_post = log_likelihood + prior_term
        if log_post > best_log_post:
            best_log_post = log_post
            best_theta = theta
        theta += grid_step

    information = 0.0
    for response, difficulty in zip(responses, difficulties):
        prob = _sigmoid(best_theta - difficulty)
        information += prob * (1.0 - prob)
    information += 1.0 / (prior_sd**2)
    se = 1.0 / math.sqrt(information) if information > 0 else float("inf")

    return {
        "theta": best_theta,
        "se": se,
        "log_posterior": best_log_post,
    }


def iq_from_theta(theta: float, *, mean: float = 100.0, sd: float = 15.0) -> float:
    return mean + sd * theta

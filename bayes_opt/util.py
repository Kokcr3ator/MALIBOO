import warnings
import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize


def acq_max(ac, gp, y_max, bounds, random_state, n_warmup=10000, n_iter=10, dataset=None,
            debug=False):
    """
    A function to find the maximum of the acquisition function

    It uses a combination of random sampling (cheap) and the 'L-BFGS-B'
    optimization method. First by sampling `n_warmup` (1e5) points at random,
    and then running L-BFGS-B from `n_iter` (250) random starting points.

    Parameters
    ----------
    ac: function
        The acquisition function object that return its point-wise value

    gp: sklearn.gaussian_process.GaussianProcessRegressor object
        A gaussian process fitted to the relevant data

    y_max: float
        The current maximum known (aka incumbent) value of the target function

    bounds: dict
        The variables bounds to limit the search of the acq max

    random_state: numpy.RandomState object
        Instance of a random number generator

    n_warmup: int, optional(default=10000)
        Number of times to randomly sample the aquisition function

    n_iter: int, optional(default=10)
        Number of times to run scipy.minimize

    dataset: pandas.DataFrame, optional(default=None)
        The (possibly reduced) domain dataset, if any, on which the maximum is to be found

    debug: bool, optional(default=False)
        Whether or not to print detailed debugging information

    Returns
    -------
    idx
        The dataset index of the arg max of the acquisition function, or None if no dataset is used
    x_max
        The arg max of the acquisition function
    """

    # Warm up with random points or dataset points
    if debug: print("Starting acq_max()\nIncumbent target: y_max =", y_max)
    if dataset is not None:
        if debug: print("Dataset passed to initial grid has shape", dataset.shape)
        x_tries = dataset.values
    else:
        if debug: print("No dataset, initial grid will be random with shape {}".format((n_warmup, bounds.shape[0])))
        x_tries = random_state.uniform(bounds[:, 0], bounds[:, 1],
                                       size=(n_warmup, bounds.shape[0]))
    ys = ac(x_tries, gp=gp, y_max=y_max)
    if debug: print("Acquisition evaluated successfully on grid")
    idx = ys.argmax()  # this index is relative to the local x_tries values matrix
    x_max = x_tries[idx]
    if debug: print("Grid index idx =", idx)

    if dataset is not None:
        # idx becomes the true dataset index of the selected point, rather than being relative to x_tries
        idx = dataset.index[idx]
        if debug: print("End of acq_max(): maximizer of utility is data[{}] = {}".format(idx, x_max))
        return idx, x_max

    max_acq = ys[idx]
    if debug: print("Best point on initial grid is ac({}) = {}".format(x_max, max_acq))

    # Explore the parameter space more throughly
    x_seeds = random_state.uniform(bounds[:, 0], bounds[:, 1],
                                   size=(n_iter, bounds.shape[0]))

    if debug: print("Calling minimize() with", len(x_seeds), "different starting seeds")

    for x_try in x_seeds:
        # Find the minimum of minus the acquisition function
        res = minimize(lambda x: -ac(x.reshape(1, -1), gp=gp, y_max=y_max),
                       x_try.reshape(1, -1),
                       bounds=bounds,
                       method="L-BFGS-B")

        # See if success
        if not res.success:
            continue
        # Store it if better than previous minimum(maximum).
        if max_acq is None or -np.squeeze(res.fun) >= max_acq:
            x_max = res.x
            max_acq = -np.squeeze(res.fun)

    if debug: print("End of acq_max(): maximizer of utility is ac({}) = {}".format(x_max, max_acq))

    # Clip output to make sure it lies within the bounds. Due to floating
    # point technicalities this is not always the case.
    return None, np.clip(x_max, bounds[:, 0], bounds[:, 1])


class UtilityFunction(object):
    """
    An object to compute the acquisition functions.

    See the maximize() function in bayesian_optimization.py for a description of the constructor arguments.
    """

    def __init__(self, kind, kappa, xi, kappa_decay=1, kappa_decay_delay=0, ml_info={}, debug=False):

        self._debug = debug
        self.kappa = kappa
        self._kappa_decay = kappa_decay
        self._kappa_decay_delay = kappa_decay_delay
        self.xi = xi
        self.kind = kind
        self._iters_counter = 0
        if ml_info:
            if self._debug: print("Initializing UtilityFunction with ml_info =", ml_info)
            for key in ('ml_target', 'ml_bounds'):  # 'target' and 'bounds' respectively in ml_info dict
                key_in_dict = key.lstrip('ml_')
                if key_in_dict not in ml_info:
                    raise ValueError("'ml_info' option must have '{}' field".format(key_in_dict))
                self.__setattr__(key, ml_info[key_in_dict])
        elif 'ml' in kind:
            raise ValueError("'ml_info' option must be provided if using '{}' acquisition".format(kind))
        if self._debug: print("UtilityFunction initialization completed")

    def update_params(self):
        self._iters_counter += 1

        if self._kappa_decay < 1 and self._iters_counter > self._kappa_decay_delay:
            self.kappa *= self._kappa_decay

    def set_ml_model(self, model):
        self.ml_model = model

    def utility(self, x, gp, y_max):
        if self.kind == 'ucb':
            return self._ucb(x, gp, self.kappa)
        if self.kind == 'ei':
            return self._ei(x, gp, y_max, self.xi)
        if self.kind == 'ei_ml':
            return self._ei_ml(x, gp, y_max, self.xi, self.ml_model, self.ml_bounds)
        if self.kind == 'eic':
            return self._eic(x, gp, y_max, self.xi, ...)  # TODO
        if self.kind == 'poi':
            return self._poi(x, gp, y_max, self.xi)
        raise NotImplementedError("The utility function {} has not been implemented.".format(self.kind))

    @staticmethod
    def _ucb(x, gp, kappa):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mean, std = gp.predict(x, return_std=True)

        return mean + kappa * std

    @staticmethod
    def _ei(x, gp, y_max, xi):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mean, std = gp.predict(x, return_std=True)
  
        a = (mean - y_max - xi)
        z = a / std
        return a * norm.cdf(z) + std * norm.pdf(z)

    @staticmethod
    def _ei_ml(x, gp, y_max, xi, ml_model, ml_bounds):
        ei = UtilityFunction._ei(x, gp, y_max, xi)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y_hat = ml_model.predict(x)
        lb, ub = ml_bounds
        indicator = np.array([lb <= y and y <= ub for y in y_hat])
        return ei * indicator

    @staticmethod
    def _eic(x, gp, y_max, xi, Gmin, Gmax, P, Q):
        """
        Compute Expected Improvement with Constraints.

        Given the target function f(x) = P(x) g(x) + Q(x), with P, Q fixed and P >= 0,
        this function multiplies the regular Expected Improvement with the probability
        that Gmin <= g(x) <= Gmax.
        """
        # Compute regular Expected Improvement
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mean, std = gp.predict(x, return_std=True)
        # std = max(std, 1e-10)
        a = (mean - y_max - xi)
        z = a / std
        ei = a * norm.cdf(z) + std * norm.pdf(z)

        # Compute probability of x respecting the constraint
        mean_Gmax = P(x) * Gmax + Q(x)
        mean_Gmin = P(x) * Gmin + Q(x)
        prob_ub = norm.cdf( (mean_Gmax - mu) / std )
        prob_lb = norm.cdf( (mean_Gmin - mu) / std )

        return ei * (prob_ub - prob_lb)

    @staticmethod
    def _poi(x, gp, y_max, xi):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mean, std = gp.predict(x, return_std=True)

        z = (mean - y_max - xi)/std
        return norm.cdf(z)


def load_logs(optimizer, logs):
    """Load previous ...

    """
    import json

    if isinstance(logs, str):
        logs = [logs]

    for log in logs:
        with open(log, "r") as j:
            while True:
                try:
                    iteration = next(j)
                except StopIteration:
                    break

                iteration = json.loads(iteration)
                try:
                    optimizer.register(
                        params=iteration["params"],
                        target=iteration["target"],
                    )
                except KeyError:
                    pass

    return optimizer


def ensure_rng(random_state=None):
    """
    Creates a random number generator based on an optional seed.  This can be
    an integer or another random state for a seeded rng, or None for an
    unseeded rng.
    """
    if random_state is None:
        random_state = np.random.RandomState()
    elif isinstance(random_state, int):
        random_state = np.random.RandomState(random_state)
    else:
        assert isinstance(random_state, np.random.RandomState)
    return random_state


class Colours:
    """Print in nice colours."""

    BLUE = '\033[94m'
    BOLD = '\033[1m'
    CYAN = '\033[96m'
    DARKCYAN = '\033[36m'
    END = '\033[0m'
    GREEN = '\033[92m'
    PURPLE = '\033[95m'
    RED = '\033[91m'
    UNDERLINE = '\033[4m'
    YELLOW = '\033[93m'

    @classmethod
    def _wrap_colour(cls, s, colour):
        return colour + s + cls.END

    @classmethod
    def black(cls, s):
        """Wrap text in black."""
        return cls._wrap_colour(s, cls.END)

    @classmethod
    def blue(cls, s):
        """Wrap text in blue."""
        return cls._wrap_colour(s, cls.BLUE)

    @classmethod
    def bold(cls, s):
        """Wrap text in bold."""
        return cls._wrap_colour(s, cls.BOLD)

    @classmethod
    def cyan(cls, s):
        """Wrap text in cyan."""
        return cls._wrap_colour(s, cls.CYAN)

    @classmethod
    def darkcyan(cls, s):
        """Wrap text in darkcyan."""
        return cls._wrap_colour(s, cls.DARKCYAN)

    @classmethod
    def green(cls, s):
        """Wrap text in green."""
        return cls._wrap_colour(s, cls.GREEN)

    @classmethod
    def purple(cls, s):
        """Wrap text in purple."""
        return cls._wrap_colour(s, cls.PURPLE)

    @classmethod
    def red(cls, s):
        """Wrap text in red."""
        return cls._wrap_colour(s, cls.RED)

    @classmethod
    def underline(cls, s):
        """Wrap text in underline."""
        return cls._wrap_colour(s, cls.UNDERLINE)

    @classmethod
    def yellow(cls, s):
        """Wrap text in yellow."""
        return cls._wrap_colour(s, cls.YELLOW)

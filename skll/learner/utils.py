"""
Utility classes and functions for SKLL learners.

:author: Nitin Madnani (nmadnani@ets.org)
:author: Michael Heilman (mheilman@ets.org)
:author: Dan Blanchard (dblanchard@ets.org)
:author: Aoife Cahill (acahill@ets.org)
:organization: ETS
"""

import inspect
import logging
import os
import sys

from functools import wraps
from importlib import import_module

import numpy as np
import scipy.sparse as sp
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.feature_selection import SelectKBest
from skll.metrics import use_score_func
from skll.utils.constants import (CLASSIFICATION_ONLY_METRICS,
                                  CORRELATION_METRICS,
                                  REGRESSION_ONLY_METRICS,
                                  UNWEIGHTED_KAPPA_METRICS,
                                  WEIGHTED_KAPPA_METRICS)


class Densifier(BaseEstimator, TransformerMixin):
    """
    A custom pipeline stage that will be inserted into the
    learner pipeline attribute to accommodate the situation
    when SKLL needs to manually convert feature arrays from
    sparse to dense. For example, when features are being hashed
    but we are also doing centering using the feature means.
    """

    def fit(self, X, y=None):
        return self

    def fit_transform(self, X, y=None):
        return self

    def transform(self, X):
        return X.todense()


class FilteredLeaveOneGroupOut(LeaveOneGroupOut):

    """
    Version of ``LeaveOneGroupOut`` cross-validation iterator that only outputs
    indices of instances with IDs in a prespecified set.

    Parameters
    ----------
    keep : set of str
        A set of IDs to keep.
    example_ids : list of str, of length n_samples
        A list of example IDs.
    """

    def __init__(self, keep, example_ids, logger=None):
        super(FilteredLeaveOneGroupOut, self).__init__()
        self.keep = keep
        self.example_ids = example_ids
        self._warned = False
        self.logger = logger if logger else logging.getLogger(__name__)

    def split(self, X, y, groups):
        """
        Generate indices to split data into training and test set.

        Parameters
        ----------
        X : array-like, with shape (n_samples, n_features)
            Training data, where n_samples is the number of samples
            and n_features is the number of features.
        y : array-like, of length n_samples
            The target variable for supervised learning problems.
        groups : array-like, with shape (n_samples,)
            Group labels for the samples used while splitting the dataset into
            train/test set.

        Yields
        -------
        train_index : np.array
            The training set indices for that split.
        test_index : np.array
            The testing set indices for that split.
        """
        for train_index, test_index in super(FilteredLeaveOneGroupOut,
                                             self).split(X, y, groups):
            train_len = len(train_index)
            test_len = len(test_index)
            train_index = [i for i in train_index if self.example_ids[i] in
                           self.keep]
            test_index = [i for i in test_index if self.example_ids[i] in
                          self.keep]
            if not self._warned and (train_len != len(train_index) or
                                     test_len != len(test_index)):
                self.logger.warning('Feature set contains IDs that are not ' +
                                    'in folds dictionary. Skipping those IDs.')
                self._warned = True

            yield train_index, test_index


class SelectByMinCount(SelectKBest):

    """
    Select features occurring in more (and/or fewer than) than a specified
    number of examples in the training data (or a CV training fold).

    Parameters
    ----------
    min_count : int, optional
        The minimum feature count to select.
        Defaults to 1.
    """

    def __init__(self, min_count=1):
        self.min_count = min_count
        self.scores_ = None

    def fit(self, X, y=None):
        """
        Fit the SelectByMinCount model.

        Parameters
        ----------
        X : array-like, with shape (n_samples, n_features)
            The training data to fit.
        y : Ignored

        Returns
        -------
        self
        """

        # initialize a list of counts of times each feature appears
        col_counts = [0 for _ in range(X.shape[1])]

        if sp.issparse(X):
            # find() is scipy.sparse's equivalent of nonzero()
            _, col_indices, _ = sp.find(X)
        else:
            # assume it's a numpy array (not a numpy matrix)
            col_indices = X.nonzero()[1].tolist()

        for i in col_indices:
            col_counts[i] += 1

        self.scores_ = np.array(col_counts)
        return self

    def _get_support_mask(self):
        """
        Returns an indication of which features to keep.
        Adapted from ``SelectKBest``.

        Returns
        -------
        mask : np.array
            The mask with features to keep set to True.
        """
        mask = np.zeros(self.scores_.shape, dtype=bool)
        mask[self.scores_ >= self.min_count] = True
        return mask


def contiguous_ints_or_floats(numbers):
    """
    Check whether the given list of numbers contains
    contiguous integers or contiguous integer-like
    floats. For example, [1, 2, 3] or [4.0, 5.0, 6.0]
    are both contiguous but [1.1, 1.2, 1.3] is not.

    Parameters
    ----------
    numbers : array-like of ints or floats
        The numbers we want to check.

    Returns
    -------
    answer : bool
        True if the numbers are contiguous integers
        or contiguous integer-like floats (1.0, 2.0, etc.)

    Raises
    ------
    TypeError
        If ``numbers`` does not contain integers or floating
        point values.
    ValueError
        If ``numbers`` is empty.
    """

    try:

        # make sure that number is not empty
        assert len(numbers) > 0

        # first check that the numbers are all integers
        # or integer-like floats (e.g., 1.0, 2.0 etc.)
        ints_or_int_like_floats = np.all(np.mod(numbers, 1) == 0)

        # next check that the successive differences between
        # the numbers are all 1, i.e., they are nuermicontiguous
        contiguous = np.all(np.diff(numbers) == 1)

    except AssertionError:
        raise ValueError('Input cannot be empty.')

    except TypeError:
        raise TypeError('Input should only contain numbers.')

    # we need both conditions to be true
    return ints_or_int_like_floats and contiguous


def get_acceptable_regression_metrics():
    """
    Return the set of metrics that are acceptable for regression.
    """

    # it's fairly straightforward for regression since
    # we do not have to check the labels
    acceptable_metrics = (REGRESSION_ONLY_METRICS |
                          UNWEIGHTED_KAPPA_METRICS |
                          WEIGHTED_KAPPA_METRICS |
                          CORRELATION_METRICS)
    return acceptable_metrics


def get_acceptable_classification_metrics(label_array):
    """
    Return the set of metrics that are acceptable given the
    the unique set of labels that we are classifying.

    Parameters
    ----------
    label_array : numpy.ndarray
        A sorted numpy array containing the unique labels
        that we are trying to predict. Optional for regressors
        but required for classifiers.

    Returns
    -------
    acceptable_metrics : set
        A set of metric names that are acceptable
        for the given classification scenario.
    """

    # this is a classifier so the acceptable objective
    # functions definitely include those metrics that
    # are specifically for classification and also
    # the unweighted kappa metrics
    acceptable_metrics = CLASSIFICATION_ONLY_METRICS | UNWEIGHTED_KAPPA_METRICS

    # now let us consider which other metrics may also
    # be acceptable depending on whether the labels
    # are strings or (contiguous) integers/floats
    label_type = label_array.dtype.type

    # CASE 1: labels are strings, then no other metrics
    # are acceptable
    if issubclass(label_type, (np.object_, str)):
        pass

    # CASE 2: labels are integers or floats; the way
    # it works in SKLL, it's guaranteed that
    # class indices will be sorted in the same order
    # as the class labels therefore, ranking metrics
    # such as various correlations should work fine.
    elif issubclass(label_type, (int,
                                 np.int32,
                                 np.int64,
                                 float,
                                 np.float32,
                                 np.float64)):
        acceptable_metrics.update(CORRELATION_METRICS)

        # CASE 3: labels are numerically contiguous integers
        # this is a special sub-case of CASE 2 which
        # represents ordinal classification. Only in this
        # case, weighted kappas -- where the distance
        # between the class labels has a special
        # meaning -- can be allowed. This is because
        # class indices are always contiguous and all
        # metrics in SKLL are computed in the index
        # space, not the label space. Note that floating
        # point numbers that are equivalent to integers
        # (e.g., [1.0, 2.0, 3.0]) are also acceptable.
        if contiguous_ints_or_floats(label_array):
            acceptable_metrics.update(WEIGHTED_KAPPA_METRICS)

    return acceptable_metrics


def load_custom_learner(custom_learner_path, custom_learner_name):
    """
    Import and load the custom learner object from the given path.

    Parameters
    ----------
    custom_learner_path : str
        The path to a custom learner.
    custom_learner_name : str
        The name of a custom learner.

    Raises
    ------
    ValueError
        If the custom learner path does not end in '.py'.

    Returns
    -------
    custom_learner_obj : skll.Learner object
        The SKLL learner object loaded from the given path.
    """
    if not custom_learner_path:
        raise ValueError('custom_learner_path was not set and learner {} '
                         'was not found.'.format(custom_learner_name))

    if not custom_learner_path.endswith('.py'):
        raise ValueError('custom_learner_path must end in .py ({})'
                         .format(custom_learner_path))

    custom_learner_module_name = os.path.basename(custom_learner_path)[:-3]
    sys.path.append(os.path.dirname(os.path.abspath(custom_learner_path)))
    import_module(custom_learner_module_name)
    return getattr(sys.modules[custom_learner_module_name], custom_learner_name)


def rescaled(cls):
    """
    Decorator to create regressors that store a min and a max for the training
    data and make sure that predictions fall within that range.  It also stores
    the means and SDs of the gold standard and the predictions on the training
    set to rescale the predictions (e.g., as in e-rater).

    Parameters
    ----------
    cls : BaseEstimator
        An estimator class to add rescaling to.

    Returns
    -------
    cls : BaseEstimator
        Modified version of estimator class with rescaled functions added.

    Raises
    ------
    ValueError
        If classifier cannot be rescaled (i.e. is not a regressor).
    """
    # If this class has already been run through the decorator, return it
    if hasattr(cls, 'rescale'):
        return cls

    # Save original versions of functions to use later.
    orig_init = cls.__init__
    orig_fit = cls.fit
    orig_predict = cls.predict

    if cls._estimator_type == 'classifier':
        raise ValueError('Classifiers cannot be rescaled. ' +
                         'Only regressors can.')

    # Define all new versions of functions
    @wraps(cls.fit)
    def fit(self, X, y=None):
        """
        Fit a model, then store the mean, SD, max and min of the training set
        and the mean and SD of the predictions on the training set.

        Parameters
        ----------
        X : array-like, with shape (n_samples, n_features)
            The data to fit.
        y : Ignored

        Returns
        -------
        self
        """

        # fit a regular regression model
        orig_fit(self, X, y=y)

        if self.constrain:
            # also record the training data min and max
            self.y_min = min(y)
            self.y_max = max(y)

        if self.rescale:
            # also record the means and SDs for the training set
            y_hat = orig_predict(self, X)
            self.yhat_mean = np.mean(y_hat)
            self.yhat_sd = np.std(y_hat)
            self.y_mean = np.mean(y)
            self.y_sd = np.std(y)

        return self

    @wraps(cls.predict)
    def predict(self, X):
        """
        Make predictions with the super class, and then adjust them using the
        stored min, max, means, and standard deviations.

        Parameters
        ----------
        X : array-like, with shape (n_samples,)
            The data to predict.

        Returns
        -------
        res : array-like
            The prediction results.
        """
        # get the unconstrained predictions
        res = orig_predict(self, X)

        if self.rescale:
            # convert the predictions to z-scores,
            # then rescale to match the training set distribution
            res = (((res - self.yhat_mean) / self.yhat_sd) * self.y_sd) + self.y_mean

        if self.constrain:
            # apply min and max constraints
            res = np.array([max(self.y_min, min(self.y_max, pred))
                            for pred in res])

        return res

    @classmethod
    @wraps(cls._get_param_names)
    def _get_param_names(class_x):
        """
        This is adapted from scikit-learns's ``BaseEstimator`` class.
        It gets the kwargs for the superclass's init method and adds the
        kwargs for newly added ``__init__()`` method.

        Parameters
        ----------
        class_x
            The the superclass from which to retrieve param names.

        Returns
        -------
        args : list
            A list of parameter names for the class's init method.

        Raises
        ------
        RunTimeError
            If `varargs` exist in the scikit-learn estimator.
        """
        try:
            init = getattr(orig_init, 'deprecated_original', orig_init)

            args, varargs, _, _ = inspect.getargspec(init)
            if varargs is not None:
                raise RuntimeError('scikit-learn estimators should always '
                                   'specify their parameters in the signature'
                                   ' of their init (no varargs).')
            # Remove 'self'
            args.pop(0)
        except TypeError:
            args = []

        rescale_args = inspect.getargspec(class_x.__init__)[0]
        # Remove 'self'
        rescale_args.pop(0)

        args += rescale_args
        args.sort()

        return args

    @wraps(cls.__init__)
    def init(self, constrain=True, rescale=True, **kwargs):
        """
        This special init function is used by the decorator to make sure
        that things get initialized in the right order.

        Parameters
        ----------
        constrain : bool, optional
            Whether to constrain predictions within min and max values.
            Defaults to True.
        rescale : bool, optional
            Whether to rescale prediction values using z-scores.
            Defaults to True.
        kwargs : dict, optional
            Arguments for base class.
        """
        # pylint: disable=W0201
        self.constrain = constrain
        self.rescale = rescale
        self.y_min = None
        self.y_max = None
        self.yhat_mean = None
        self.yhat_sd = None
        self.y_mean = None
        self.y_sd = None
        orig_init(self, **kwargs)

    # Override original functions with new ones
    cls.__init__ = init
    cls.fit = fit
    cls.predict = predict
    cls._get_param_names = _get_param_names
    cls.rescale = True

    # Return modified class
    return cls


def train_and_score(learner,
                    train_examples,
                    test_examples,
                    metric):
    """
    A utility method to train a given learner instance on the given training examples,
    generate predictions on the training set itself and also the given
    test set, and score those predictions using the given metric.
    The method returns the train and test scores.

    Note that this method needs to be a top-level function since it is
    called from within ``joblib.Parallel()`` and, therefore, needs to be
    picklable which it would not be as an instancemethod of the ``Learner``
    class.

    Parameters
    ----------
    learner : skll.Learner
        A SKLL ``Learner`` instance.
    train_examples : array-like, with shape (n_samples, n_features)
        The training examples.
    test_examples : array-like, of length n_samples
        The test examples.
    metric : str
        The scoring function passed to ``use_score_func()``.

    Returns
    -------
    train_score : float
        Output of the score function applied to predictions of
        ``learner`` on ``train_examples``.
    test_score : float
        Output of the score function applied to predictions of
        ``learner`` on ``test_examples``.
    """

    _ = learner.train(train_examples, grid_search=False, shuffle=False)
    train_predictions = learner.predict(train_examples)
    test_predictions = learner.predict(test_examples)
    if learner.model_type._estimator_type == 'classifier':
        test_label_list = np.unique(test_examples.labels).tolist()
        unseen_test_label_list = [label for label in test_label_list
                                  if label not in learner.label_list]
        unseen_label_dict = {label: i for i, label in enumerate(unseen_test_label_list,
                                                                start=len(learner.label_list))}
        # combine the two dictionaries
        train_and_test_label_dict = learner.label_dict.copy()
        train_and_test_label_dict.update(unseen_label_dict)
        train_labels = np.array([train_and_test_label_dict[label]
                                 for label in train_examples.labels])
        test_labels = np.array([train_and_test_label_dict[label]
                                for label in test_examples.labels])
    else:
        train_labels = train_examples.labels
        test_labels = test_examples.labels

    train_score = use_score_func(metric, train_labels, train_predictions)
    test_score = use_score_func(metric, test_labels, test_predictions)
    return train_score, test_score

import numpy as np

from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.utils.validation import check_is_fitted

from skorecard.linear_model import LogisticRegression
from skorecard.utils import BucketerTypeError
from skorecard.utils.validation import ensure_dataframe, is_fitted
from skorecard.pipeline import BucketingProcess, to_skorecard_pipeline
from skorecard.preprocessing import WoeEncoder
from skorecard.bucketers import (
    OrdinalCategoricalBucketer,
    DecisionTreeBucketer,
    OptimalBucketer,
)
from skorecard.preprocessing import ColumnSelector

from typing import List


class Skorecard(BaseEstimator, ClassifierMixin):
    """Scikit-learn-like estimator that builds a scorecard model.

    Usage Examples:
    usage of Skorecard() without bucketing

    ```python
    from skorecard import Skorecard
    from skorecard.datasets import load_uci_credit_card
    X,y = load_uci_credit_card(return_X_y=True)

    num_cols = ["LIMIT_BAL", "BILL_AMT1"]
    cat_cols = ["EDUCATION", "MARRIAGE"]
    model = Skorecard(cat_features = cat_cols, variables = num_cols+cat_cols)

    model.fit(X, y)
    ```

    Alternatively, by passing a predefined Bucketing Process.

    ```python
    from skorecard import Skorecard
    from skorecard.pipeline import BucketingProcess
    from skorecard.bucketers import DecisionTreeBucketer, OrdinalCategoricalBucketer, OptimalBucketer
    from skorecard.datasets import load_uci_credit_card
    from sklearn.pipeline import make_pipeline

    X,y = load_uci_credit_card(return_X_y=True)
    num_cols = ["LIMIT_BAL", "BILL_AMT1"]
    cat_cols = ["EDUCATION", "MARRIAGE"]

    prebucketing_pipeline = make_pipeline(
        DecisionTreeBucketer(variables=num_cols, max_n_bins=100),
        OrdinalCategoricalBucketer(variables=cat_cols, tol=0.01)
    )
    bucketing_pipeline = make_pipeline(
        OptimalBucketer(variables=features, max_n_bins=5, min_bin_size=0.08),
        OptimalBucketer(variables=cat_cols, variables_type="categorical", max_n_bins=5, min_bin_size=0.08)
    )

    bucketer = BucketingProcess(prebucketing_pipeline = prebucketing_pipeline, bucketing_pipeline = bucketing_pipeline)

    skorecard_model = Skorecard(bucketing=bucketer, variables=num_cols+cat_cols)

    skorecard_model.fit(X, y)

    # Details
    skorecard_model.bucket_table("LIMIT_BAL")
    skorecard_model.plot_bucket("LIMIT_BAL")
    skorecard_model.prebucket_table("LIMIT_BAL")
    skorecard_model.plot_prebucket("LIMIT_BAL")
    ```

    Using it with bucketers
     ```python
    from skorecard import Skorecard
    from skorecard.bucketers import DecisionTreeBucketer, OrdinalCategoricalBucketer
    from skorecard.datasets import load_uci_credit_card
    from sklearn.pipeline import make_pipeline

    X,y = load_uci_credit_card(return_X_y=True)
    num_cols = ["LIMIT_BAL", "BILL_AMT1"]
    cat_cols = ["EDUCATION", "MARRIAGE"]

    bucket_pipe  = make_pipeline(
        DecisionTreeBucketer(variables=num_cols, max_n_bins=5),
        OrdinalCategoricalBucketer(variables=cat_cols, tol=0.05)
    )

    skorecard_model = Skorecard(bucketing=bucket_pipe, variables=num_cols+cat_cols)

    skorecard_model.fit(X, y)

    # Details
    skorecard_model.bucket_table("LIMIT_BAL")
    skorecard_model.plot_bucket("LIMIT_BAL")
    ```

    """

    def __init__(
        self,
        bucketing=None,
        *,
        specials: dict = {},
        encoder: str = "woe",
        variables: List = [],
        verbose: int = 0,
        lr_kwargs: dict = None,
    ):
        """
        Init the class.

        Args:
            bucketing: bucketing step, can be a bucketer, a pipeline of bucketers or a BucketingProcess.
                    Default value is None. In that case it will generate a predefined BucketingProcess where
                    numerical and categorical columns will attempt to be auto-detected and bucketed accordingly.
            specials: (nested) dictionary of special values that require their own binning.
                    Used only if bucketing=None.
                    The dictionary has the following format:
                        {"<column name>" : {"name of special bucket" : <list with 1 or more values>}}
                    For every feature that needs a special value, a dictionary must be passed as value.
                    This dictionary contains a name of a bucket (key) and an array of unique values that should be put
                    in that bucket.
                    When special values are defined, they are not considered in the fitting procedure.
            encoder (string): indicating the type of encoder. Currently only 'woe' (weight-of-evidence) is supported.
            variables (list): list of features after bucketing to fit the LogisticRegression model on. Defaults to None (all features selected).
            verbose (int): verbosity, set to 0 to avoid warning methods.
            lr_kwargs (dict): Settings passed to sklearn.linear_model.LogisticRegression.
                By default no settings are passed.
        """  # noqa
        self.bucketing = bucketing
        self.specials = specials
        self.encoder = encoder
        self.variables = variables
        self.verbose = verbose
        self.lr_kwargs = lr_kwargs

    def __repr__(self):
        """Pretty print self.

        Returns:
            str: reproducable object representation.
        """
        vars = ""
        for k, v in self.__dict__.items():
            if k == "bucketing":
                v = str(v.__class__.__name__)  # get only the class name of the bucketer
            vars += f"{k}={v}, "

        return f"{self.__class__.__name__}({vars})"

    def _build_default_bucketing_process(self, X):
        """
        A default BucketingProcess to use if not specified by user.
        """
        # Auto-detect cat and numerical columns
        num_features = list(X._get_numeric_data().columns)
        cat_features = [f for f in X.columns if f not in num_features]

        if len(cat_features) > 0:
            prebucketing_pipe = [
                DecisionTreeBucketer(variables=num_features, max_n_bins=50, min_bin_size=0.02),
                OrdinalCategoricalBucketer(variables=cat_features, tol=0.02),
            ]
            bucketing_pipe = [
                OptimalBucketer(variables=num_features, max_n_bins=6, min_bin_size=0.05),
                OptimalBucketer(
                    variables=cat_features,
                    variables_type="categorical",
                    max_n_bins=6,
                    min_bin_size=0.05,
                ),
            ]
        else:
            prebucketing_pipe = [DecisionTreeBucketer(variables=num_features, max_n_bins=50, min_bin_size=0.02)]
            bucketing_pipe = [OptimalBucketer(variables=num_features, max_n_bins=6, min_bin_size=0.05)]

        prebucketing_pipeline = to_skorecard_pipeline(make_pipeline(*prebucketing_pipe))
        bucketing_pipeline = to_skorecard_pipeline(make_pipeline(*bucketing_pipe))

        return BucketingProcess(
            specials=self.specials, prebucketing_pipeline=prebucketing_pipeline, bucketing_pipeline=bucketing_pipeline
        )

    def _build_pipeline(self, X):
        """Build the default pipeline."""
        if self.encoder == "woe":
            encoder = WoeEncoder()
        else:
            raise NotImplementedError(f"Encoder {self.encoder} not supported. Please use 'woe'")

        if self.lr_kwargs:
            lr_model = LogisticRegression(**self.lr_kwargs)
        else:
            lr_model = LogisticRegression()

        # If a pipeline is specified, convert to SkorecardPipeline
        if self.bucketing is None:
            self.bucketing_ = self._build_default_bucketing_process(X)
        elif isinstance(self.bucketing, Pipeline):
            self.bucketing_ = to_skorecard_pipeline(self.bucketing)
        else:
            self.bucketing_ = self.bucketing

        # Note ColumnSelector will not select any columns if passed an empty list.
        self.pipeline_ = Pipeline(
            [
                ("bucketer", self.bucketing_),
                ("encoder", encoder),
                ("column_selector", ColumnSelector(self.variables)),
                ("model", lr_model),
            ]
        )

    def fit(self, X, y=None):
        """Fit the skorecard pipeline with X, y.

        Args:
            X (pd.DataFrame): features
            y (pd.Series, optional): target. Defaults to None.
        """
        # input validation
        assert isinstance(self.variables, list)
        # data validation
        X = ensure_dataframe(X)

        self._build_pipeline(X)

        try:
            self.pipeline_.fit(X, y)
        except np.linalg.LinAlgError as e:
            # update the LinAlgError message with a more helpful message.
            error_msg = (
                "\nThe LinAlgError is very likely caused by multi-collinear variables."
                "Check for multi-collinearity by calling \n"
                "model.woe_transform(X).corr()"
                "\nThis is probably due to at least two features being bucketed to one unique bucket.\n"
                "Find those features by running\n"
                "model.bucket_transform(X_train).nunique()"
            )
            raise type(e)(f"{e.args[0]}\n{error_msg}")

        return self

    def fit_interactive(self, X, y=None, mode="external", **server_kwargs):
        """
        Fit a bucketer and then interactive edit the fit using a dash app.

        Note we are using a [jupyterdash](https://medium.com/plotly/introducing-jupyterdash-811f1f57c02e) app,
        which supports 3 different modes:

        - 'external' (default): Start dash server and print URL
        - 'inline': Start dash app inside an Iframe in the jupyter notebook
        - 'jupyterlab': Start dash app as a new tab inside jupyterlab

        """
        # We need to make sure we only fit if not already fitted
        # This prevents a user losing manually defined boundaries
        # when re-running .fit_interactive()
        if not is_fitted(self):
            self.fit(X, y)
        self.bucketing_.fit_interactive(X=X, y=y, mode=mode, **server_kwargs)

    def bucket_transform(self, X):
        """
        Transform X through the bucketing pipelines.
        """
        check_is_fitted(self)
        return self.pipeline_[0].transform(X)

    def woe_transform(self, X):
        """Transform X through the bucketing + WoE pipelines."""
        check_is_fitted(self)
        return self.pipeline_[:2].transform(X)

    def predict_proba(self, X):
        """Predicted probabilities."""
        check_is_fitted(self)
        return self.pipeline_.predict_proba(X)

    def predict(self, X):
        """Predict the class."""
        check_is_fitted(self)
        return self.pipeline_.predict(X)

    def get_stats(self):
        """Get the stats of the fitted model."""
        check_is_fitted(self)
        # Get the pipeline model
        model = self.pipeline_[-1]
        check_is_fitted(model)
        return model.get_stats()

    def summary(self):
        """Get the summary of all the buceketers."""
        raise NotImplementedError("Not implemented yet")
        check_is_fitted(self)
        return self.bucketing_.summary()

    def prebucket_table(self, column):
        """Get the prebucket_table.

        It's supported only if the bucketing object is BucketingProcess.

        Args:
            column: (str). column name

        Returns: pd.DataFrame, pre-bucketing summary for columns
        """
        check_is_fitted(self)
        if isinstance(self.bucketing_, BucketingProcess):
            return self.bucketing_.prebucket_table(column)
        else:
            error_msg = (
                f"prebucket_table is supported only if the attirbuite bucketing is of type BucketingProcess, "
                f"got {self.bucketing_.__class__}"
            )
            raise BucketerTypeError(error_msg)

    def bucket_table(self, column):
        """Get the bucket table.

        Args:
            column: (str). column name

        Returns: pd.DataFrame, -bucketing summary for columns
        """
        check_is_fitted(self)
        return self.bucketing_.bucket_table(column)

    def plot_prebucket(self, column):
        """Plot the prebucket_table. It's supported only if the bucketing object is BucketingProcess.

        Args:
            column: (str). column name
        """
        if isinstance(self.bucketing_, BucketingProcess):
            return self.bucketing_.plot_prebucket(column)
        else:
            error_msg = (
                f"plot_prebucket  is supported only if the attirbuite bucketing is of type BucketingProcess, "
                f"got {type(self.bucketing_.__class__)}"
            )
            raise BucketerTypeError(error_msg)

    def plot_bucket(self, column):
        """Plot the buckets.

        Args:
            column: (str). column name
        """
        check_is_fitted(self)
        return self.bucketing_.plot_bucket(column)

    def _more_tags(self):
        """
        Estimator tags are annotations of estimators that allow programmatic inspection of their capabilities.

        See https://scikit-learn.org/stable/developers/develop.html#estimator-tags
        """  # noqa
        return {"binary_only": True, "allow_nan": True}

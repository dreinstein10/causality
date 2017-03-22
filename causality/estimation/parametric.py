import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.robust.robust_linear_model import RLM
from statsmodels.discrete.discrete_model import Logit
from sklearn.neighbors import NearestNeighbors


class DifferenceInDifferences(object):
    def __init__(self, robust=True):
        """
        We will take a dataframe where each row is a user,
        and the columns are:
        (1) Assignment: 1 = test, 0 = control
        (2) Start: The value of the metric you're interested
            in at the start of the experiment.
        (3) End: The value of the metric you're interested in
            at the end of the experiment.
        """
        if robust:
            self.model = RLM
        else:
            self.model = OLS

    def average_treatment_effect(self, X, start='Start', end='End', assignment='Assignment'):
        test = X[X['Assignment']==1][['Start','End']]
        control = X[X['Assignment']==0][['Start','End']]
        del X

        test_initial = test['Start']
        test_final = test['End']
        control_initial = control['Start']
        control_final = control['End']
        del test, control

        df = pd.DataFrame({'y' : test_initial, 
                   'assignment' : [1. for i in test_initial], 
                   't' :[0. for i in test_initial] })
        df = df.append(pd.DataFrame({'y' : test_final, 
                                     'assignment' : [1. for i in test_final], 
                                     't' :[1. for i in test_final] }))

        df = df.append(pd.DataFrame({'y' : control_initial, 
                                     'assignment' : [0. for i in control_initial], 
                                     't' :[0. for i in control_initial] }))

        df = df.append(pd.DataFrame({'y' : control_final, 
                                     'assignment' : [0. for i in control_final], 
                                     't' :[1. for i in control_final] }))
        del test_initial, test_final, control_initial, control_final
        df['did'] = df['t'] * df['assignment'] 
        df['intercept'] = 1.

        model = self.model(df['y'], df[['t', 'assignment','did', 'intercept']])
        result = model.fit()
        conf_int = result.conf_int().ix['did']
        expected = result.params['did']
        return conf_int[0], expected, conf_int[1]
        
    def test_parallel_trend(self, X, start='Start', end='End', assignment='Assignment'):
        """
        This will find the average treatment effect on
        a dataset before the experiment is run, to make
        sure that it is zero.  This tests the assumption
        that the average treatment effect between the test
        and control groups when neither is treated is 0.

        The format for this dataset is the same as that 
        for the real estimation task, except that the start
        time is some time before the experiment is run, and
        the end time is the starting point for the experiment.
        """
        lower, exp, upper = self.average_treatment_effect(X,start=start, end=end, assignment=assignment)
        if lower <= 0 <= upper:
            return True
        return False


class PropensityScoreMatching(object):
    def __init__(self):
        # change the model if there are multiple matches per treated!
        self.propensity_score_model = None


    def score(self, X, confounder_types, assignment='assignment', store_model_fit=False, intercept=True):
        """
        Fit a propensity score model using the data in X and the confounders listed in confounder_types. This adds
        the propensity scores to the dataframe, and returns the new dataframe.

        :param X: The data set, with (at least) an assignment, set of confounders, and an outcome
        :param assignment: A categorical variable (currently, 0 or 1) indicating test or control group, resp.
        :param outcome: The outcome of interest.  Should be real-valued or ordinal.
        :param confounder_types: A dictionary of variable_name: variable_type pairs of strings, where
        variable_type is in {'c', 'o', 'd'}, for 'continuous', 'ordinal', and 'discrete'.
        :param store_model_fit: boolean, Whether to store the model as an attribute of the class, as
        self.propensity_score_model
        :param intercept: Whether to include an intercept in the logistic regression model
        :return: A new dataframe with the propensity scores included
        """
        df = X[[assignment]]
        regression_confounders = []
        for confounder, var_type in confounder_types.items():
            if var_type == 'o' or var_type == 'u':
                c_dummies = pd.get_dummies(X[[confounder]], prefix=confounder)
                if len(c_dummies.columns) == 1:
                    df[c_dummies.columns] = c_dummies[c_dummies.columns]
                    regression_confounders.extend(c_dummies.columns)
                else:
                    df[c_dummies.columns[1:]] = c_dummies[c_dummies.columns[1:]]
                    regression_confounders.extend(c_dummies.columns[1:])
            else:
                regression_confounders.append(confounder)
                df.loc[:,confounder] = X[confounder].copy() #
                df.loc[:,confounder] = X[confounder].copy() #
        if intercept:
            df.loc[:,'intercept'] = 1.
            regression_confounders.append('intercept')
        logit = Logit(df[assignment], df[regression_confounders])
        model = logit.fit()
        if store_model_fit:
            self.propensity_score_model = model
        X.loc[:,'propensity score'] = model.predict(df[regression_confounders])
        return X

    def match(self, X, assignment='assignment', score='propensity score', n_neighbors=2):
        """
        For each unit in the test group, match n_neighbors units in the control group with the closest propensity scores
        (matching with replacement).

        :param X: The data set, with (at least) an assignment, set of confounders, and an outcome
        :param assignment: A categorical variable (currently, 0 or 1) indicating test or control group, resp.
        :param score: The name of the column in X containing the propensity scores. Default is 'propensity score'
        :param n_neighbors: The number of neighbors to match to each unit.
        :return: two dataframes. the first contains the treatment units, and the second contains all of the control units
        that have been matched to the treatment units. The treatment unit dataframe (first dataframe) contains a new
        column with the indices of the matches in the control dataframe called 'matches'.
        """

        treatments = X[X[assignment] == 1]
        control = X[X[assignment] == 0]
        neighbor_search = NearestNeighbors(metric='euclidean', n_neighbors=n_neighbors)
        neighbor_search.fit(control[[score]].values)
        treatments.loc[:, 'matches'] = treatments[score].apply(lambda x: neighbor_search.kneighbors(x)[1])
        return treatments, control

    def estimate_treatments(self, treatments, control, outcome):
        """
        Find the average outcome of the matched control units for each treatment unit. Add it to the treatment dataframe
        as a new column called 'control outcome'.

        :param treatments: A dataframe containing at least an outcome, and a list of indices for matches (in the control
        dataframe). This should be generated as the output of the self.match method.
        :param control: The dataframe containing the matches for the treatment dataframe. This should be generated as
        the output of the self.match method.
        :param outcome: A float or ordinal representing the outcome of interest.
        :return: The treatment dataframe with the matched control outcome for each unit in a new column,
        'control outcome'.
        """

        def get_matched_outcome(matches):
            return sum([control[outcome].values[i] / float(len(matches[0])) for i in matches[0]])
        treatments.loc[:,'control outcome'] = treatments['matches'].apply(get_matched_outcome)
        return treatments

    def estimate_ATT(self, X, assignment, outcome, confounder_types, n_neighbors=5):
        """
        Estimate the average treatment effect for people who normally take the test assignment. Assumes a 1 for
        the test assignment, 0 for the control assignment.

        :param X: The data set, with (at least) an assignment, set of confounders, and an outcome
        :param assignment: A categorical variable (currently, 0 or 1) indicating test or control group, resp.
        :param outcome: The outcome of interest.  Should be real-valued or ordinal.
        :param confounder_types: A dictionary of variable_name: variable_type pairs of strings, where
        variable_type is in {'c', 'o', 'd'}, for 'continuous', 'ordinal', and 'discrete'.
        :param n_neighbors: An integer for the number of neighbors to use with k-nearest-neighbor matching
        :return: a float representing the treatment effect on the treated
        """
        X = self.score(X, confounder_types, assignment)
        treatments, control = self.match(X, assignment=assignment, score='propensity score', n_neighbors=n_neighbors)
        treatments = self.estimate_treatments(treatments, control, outcome)
        y_hat_treated = treatments[outcome].mean()
        y_hat_control = treatments['control outcome'].mean()
        return y_hat_treated - y_hat_control

    def estimate_ATC(self, X, assignment, outcome, confounder_types, n_neighbors=5):
        """
        Estimate the average treatment effect for people who normally take the control assignment. Assumes a 1 for
        the test assignment, 0 for the control assignment.

        :param X: The data set, with (at least) an assignment, set of confounders, and an outcome
        :param assignment: A categorical variable (currently, 0 or 1) indicating test or control group, resp.
        :param outcome: The outcome of interest.  Should be real-valued or ordinal.
        :param confounder_types: A dictionary of variable_name: variable_type pairs of strings, where
        variable_type is in {'c', 'o', 'd'}, for 'continuous', 'ordinal', and 'discrete'.
        :param n_neighbors: An integer for the number of neighbors to use with k-nearest-neighbor matching
        :return: a float representing the treatment effect on the control
        """
        df = X.copy()
        df[assignment] = (df[assignment] + 1) % 2
        return -self.estimate_ATT(df, assignment, outcome, confounder_types, n_neighbors=n_neighbors)

    def estimate_ATE(self, X, assignment, outcome, confounder_types, n_neighbors=5):
        """
        Find the Average Treatment Effect(ATE) on the population. An ATE can be estimated as a weighted average of the
        ATT and ATC, weighted by the proportion of the population who is treated or not, resp. Assumes a 1 for
        the test assignment, 0 for the control assignment.

        :param X: The data set, with (at least) an assignment, set of confounders, and an outcome
        :param assignment:  A categorical variable (currently, 0 or 1) indicating test or control group, resp.
        :param outcome: The outcome of interest.  Should be real-valued or ordinal.
        :param confounder_types: A dictionary of variable_name: variable_type pairs of strings, where
        variable_type is in {'c', 'o', 'd'}, for 'continuous', 'ordinal', and 'discrete'.
        :param n_neighbors: An integer for the number of neighbors to use with k-nearest-neighbor matching
        :return: a float representing the average treatment effect
        """
        att = self.estimate_ATT(X, assignment, outcome, confounder_types, n_neighbors=n_neighbors)
        atc = self.estimate_ATC(X, assignment, outcome, confounder_types, n_neighbors=n_neighbors)
        p_assignment = len(X[X[assignment] == 1]) / float(len(X))
        return p_assignment*att + (1-p_assignment)*atc

    def assess_balance(self, X, treated, control, assignment, confounders):
        pass

    def calculate_balance(self, X, x, d):
        numerator = X[X[d] == 1].mean()[x] - X[X[d] == 0].mean()[x]
        denominator = np.sqrt((X[X[d] == 1].var()[x] + X[X[d] == 0].var()[x])/2.)
        return numerator / denominator
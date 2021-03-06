from sklearn.multioutput import ClassifierChain

from nptyping import NDArray
import numpy as np
from typing import List


class BaseInferer:
    """Base abstract class for infererence algorithms.
    """

    def __init__(self, cc: ClassifierChain, loss: str = 'exact_match'):
        """Default constructor.

        Args:
            order (List[int]): Order of the classifier chain estimators.
            loss (str): 'exact_match' or 'hamming', specifying which loss this prediction
                should minimize.
        """

        self._cc = cc
        assert(loss == 'exact_match' or loss == 'hamming')
        self.loss = loss

    def infer(self, x: NDArray[float]):
        """Infers a prediction according to the child inferer algorithm.

        Args:
            x (np.array): Prediction data of shape (n, d1).

        Returns:
            np.array: Prediction outputs of shape (n, d2).
            int: The average number of visited nodes in the tree search.
        """

        pred, n_nodes = self._infer(x)
        reward = self.__calculate_reward(x, pred)
        pred = self.__fix_order(pred)
        return pred, n_nodes, reward

    def _infer(self, x: NDArray[float]):
        """Virtual method to do inference.

        Args:
            x (np.array): Prediction data of shape (n, d1).

        Raises:
            NotImplementedError: This method is virtual.
        """        

        raise NotImplementedError

    def _new_score(self, past_score: NDArray[float], new_proba: NDArray[float]):
        """Updates the current score in the tree path. This depends on the loss function
        being used: if 'exact_match', this score is the conditional probability and if
        'hamming', it is the sum of probabilities.

        Args:
            past_score (np.array): Scores until this estimator, shape (n,)
            new_proba (np.array): Probabilities on the new estimator prediction, shape
                (n,)

        Returns:
            np.array: Score of this new prediction, shape (n,)
        """

        if self.loss == 'exact_match':
            return past_score * new_proba
        else:
            return past_score + new_proba

    def __calculate_reward(self, x: NDArray[float], pred: NDArray[float]):
        """Calculates the final reward given a path of predictions.

        Args:
            x (np.array): Prediction data of shape (n, d1).
            pred (np.array): Predicted labels of shape (n, d2).

        Returns:
            reward (float): Final average reward of that prediction.
        """
        if self.loss == 'exact_match':
            reward = np.ones((len(x),), dtype=float)
        else:
            reward = np.zeros((len(x),), dtype=float)

        for i in range(len(self._cc.estimators_)):
            x_aug = np.hstack((x, pred[:, :i]))
            proba = self._cc.estimators_[i].predict_proba(x_aug)
            reward = self._new_score(
                reward,
                np.take_along_axis(
                    proba, pred[:, i].astype(int).reshape(-1, 1), axis=1).flatten())
        return reward.mean()

    def __fix_order(self, pred: NDArray[float]):
        """Estimators in classifier chain are not necessarily in the label order. This
        method reorders the prediction to the label order.

        Args:
            pred (np.array): Prediction in the estimators order of shape (n,).

        Returns:
            np.array: Prediction in the correct order of shape (n,).
        """

        inv_order = np.empty_like(self._cc.order_)
        inv_order[self._cc.order_] = np.arange(len(self._cc.order_))
        return pred[:, inv_order]

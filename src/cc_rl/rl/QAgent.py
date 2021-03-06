import numpy as np
import torch
from torch import Tensor
from torch.utils import data
from typing import List, Callable

from cc_rl.gym_cc.Env import Env
from cc_rl.rl.Agent import Agent
from cc_rl.rl.QModel import QModel


class QAgent(Agent):
    """
    Reinforcement learning agent that uses only Q-learning to find the best tree path.
    """

    def __init__(self, environment: Env):
        super().__init__(environment)
        self.model = QModel(environment.classifier_chain.n_labels + 1, self.device)
        self.data_loader = None
        self.dataset = None
        self.best_path = None
        self.best_path_reward = -np.inf
        self.n_visited_nodes = 0
        self.node_to_best_final_value = {}

    def train(self, nb_sim: int, nb_paths: int, epochs: int, batch_size: int = 64,
              learning_rate: float = 1e-2, verbose: bool = False):
        """
        Trains model from the environment given in the constructor, going through the tree
        nb_sim * nb_paths times.
        @param nb_sim: Number of training loops that will be executed.
        @param nb_paths: Number of paths explored in each step.
        @param epochs: Number of epochs in each training step.
        @param batch_size: Used in training.
        @param learning_rate: Used in training.
        @param verbose: Will print train execution if True.
        """

        optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)
        loss_fn = torch.nn.MSELoss()
        self.n_visited_nodes = 0

        for sim in range(nb_sim):
            self.__experience_environment(nb_paths, batch_size)
            self.__train_once(epochs, optimizer, loss_fn, verbose)

    def predict(self, return_num_nodes: bool = False, return_reward: bool = False,
                mode: str = 'best_visited'):
        """
        Predicts the best path after the training step is done.
        @param return_num_nodes: If true, will also return the total number of predictions
            ran by estimators in the classifier chain in total by the agent.
        @param return_reward: If true, will also return the reward got in this path.
        @param mode: If 'best_visited', will get the path with the best reward found
            during training. If 'final_decision', will go through the tree one last time
            to find the path.
        @return: (np.array) Prediction outputs of shape (n, d2).
                 (int, optional): The average number of visited nodes in the tree search.
        """

        if mode == 'best_visited':
            path = self.best_path
            reward = self.best_path_reward
        elif mode == 'final_decision':
            actions_history = []
            final_values = []
            self.__experience_environment_once(actions_history, [], final_values, 0)
            path = actions_history[-1]
            reward = final_values[-1]
        else:
            raise ValueError

        path = (path + 1).astype(bool)
        returns = [path]
        if return_reward:
            returns.append(reward)
        if return_num_nodes:
            returns.append(self.n_visited_nodes)
        return tuple(returns)

    def __experience_environment(self, nb_paths: int, batch_size: int, exploring_p=0.5):
        """
        In this method the model is used to predict the best path for a total of
        nb_paths paths. For each decision the model takes, the state is recorded.
        The result is then stored in the variable self.data_loader
        @param nb_paths: Number of paths that must be experiences from top to bottom, i.e.
            number of resets on the environment.
        @param batch_size: To be used in training.
        @param exploring_p: Probability that, when exploring, the path will be chosen
            randomly instead of predicted by the model.
        """

        # Resetting history
        actions_history = []
        probas_history = []
        final_values = []

        for i in range(nb_paths):
            self.__experience_environment_once(actions_history, probas_history,
                                               final_values, exploring_p=exploring_p)

        # Updating data loader to train the network
        actions_history = torch.tensor(actions_history).float()
        probas_history = torch.tensor(probas_history).float()
        final_values = torch.tensor(final_values).float()

        # TODO: put a limit in the size of the dataset
        new_data = data.TensorDataset(actions_history, probas_history, final_values)
        if self.dataset is None:
            self.dataset = new_data
        else:
            self.dataset = data.ConcatDataset([self.dataset, new_data])
        self.data_loader = data.DataLoader(self.dataset, batch_size=batch_size,
                                           shuffle=True)

    def __train_once(self, epochs: int, optimizer: torch.optim.Optimizer,
                     loss_fn: Callable[[Tensor, Tensor], Tensor], verbose: bool):
        """
        Fits the model with the data that is currently in self.data_loader.
        @param epochs: Used in training.
        @param optimizer: Used in training.
        @loss_fn: Used in training.
        @param verbose: Will print train execution if True.
        """
        # Start training
        self.model.train()

        for epoch in range(epochs):
            for i, data in enumerate(self.data_loader):
                actions_history, probas_history, final_values = \
                    [d.to(self.device) for d in data]

                optimizer.zero_grad()

                # Calculate Q value for each test case
                predict = self.model(actions_history, probas_history).flatten()

                # Apply loss function
                loss = loss_fn(predict, final_values)

                # Brackprop and optimize
                loss.backward()
                optimizer.step()

                if verbose:
                    print('Epoch[{}/{}], Step [{}/{}], Loss: {:.4f}'.format(
                        epoch + 1, epochs, i + 1, len(self.data_loader),
                        loss.item() / self.data_loader.batch_size))

    def __experience_environment_once(self, actions_history: List[Tensor],
                                      probas_history: List[Tensor],
                                      final_values: List[float],
                                      exploring_p: float):
        # Each path should go until the end
        depth = self.environment.classifier_chain.n_labels

        # Get current state from environment
        next_proba, action_history, proba_history = self.environment.reset()

        # Record the nodes in the current path
        nodes_current_path = []

        for j in range(depth):
            r = np.random.rand()
            if r < exploring_p:
                # Add randomness to make agent explore more
                next_action = np.random.randint(0, 2) * 2 - 1
            else:
                # Choosing the next action using the agent
                next_action = self.model.choose_action(
                    torch.tensor(action_history).float(),
                    torch.tensor(proba_history).float(),
                    next_proba, j)

            # Get next state
            next_proba, action_history, proba_history, final_value, end = \
                self.environment.step(next_action)
            self.n_visited_nodes += 1

            # Adding past actions to the history
            nodes_current_path += [tuple(action_history)]
            actions_history += [action_history]
            probas_history += [proba_history]

        # Updating the history for the final values
        for node in nodes_current_path:
            if node not in self.node_to_best_final_value:
                max_final_value = final_value
            else:
                max_final_value = max(self.node_to_best_final_value[node], final_value)
            self.node_to_best_final_value[node] = max_final_value
            final_values += [max_final_value]

        # Store best path for prediction
        if final_value > self.best_path_reward:
            self.best_path_reward = final_value
            self.best_path = actions_history[-1]

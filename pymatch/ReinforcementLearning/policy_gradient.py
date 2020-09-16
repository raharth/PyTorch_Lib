import torch
from torch.utils.data.sampler import SubsetRandomSampler
from torch.distributions import Categorical
import torch.nn.functional as F

from tqdm import tqdm
import numpy as np

from pymatch.DeepLearning.hat import EnsembleHatStd
from pymatch.ReinforcementLearning.memory import Memory
from pymatch.ReinforcementLearning.loss import REINFORCELoss
from pymatch.DeepLearning.learner import Learner


class PolicyGradientActionSelection:
    """
    Probability based selection strategy, used for Policy Gradient
    """
    def __call__(self, agent, observation):
        agent.model.to(agent.device)
        # agent.model.eval()
        probs = agent.model(observation.to(agent.device))
        dist = Categorical(probs.squeeze())
        action = dist.sample()
        log_prob = dist.log_prob(action)
        return action.item(), log_prob


class BayesianDropoutPGActionSelection:
    def __init__(self, predictions: int, reduce_hat=EnsembleHatStd()):
        """
        Probability based selection strategy, used for Policy Gradient, using multiple drop out forward passes
        to estimate the reliability of the prediction.

        Args:
            predictions:    number of iterations used for the bayesian ensemble
            reduce_hat:     ensemble hat reducing the ouput of the ensemble to a single probability distribution
        """
        self.predictions = predictions
        self.reduce_hat = reduce_hat

    def __call__(self, agent, observation):
        observation = observation.to(agent.device)
        agent.model.to(agent.device)
        action_probs = agent.model(torch.cat(self.predictions * [observation]))
        prob_mean, prob_std = self.reduce_hat(action_probs)
        dist = Categorical(prob_mean.squeeze())
        action = dist.sample()
        log_prob = dist.log_prob(action)
        return action.item(), log_prob


class QActionSelection:
    def __init__(self, temperature=1.):
        """
        Temperature based exponential selection strategy

        Args:
            temperature:
        """
        self.temperature = temperature

    def __call__(self, agent, observation):
        agent.model.to(agent.device)
        qs = agent.model(observation.to(agent.device))
        probs = F.softmax(qs / self.temperature, dim=1)
        dist = Categorical(probs.squeeze())
        action = dist.sample()
        return action.item()


class EpsilonGreedyActionSelection:
    def __init__(self, action_space, epsilon=.9):
        """
        Epsilon greedy selection strategy, choosing the best or with p=1-epsilon choosing a random action

        Args:
            action_space:   list of possible actions
            epsilon:        probability for max
        """
        self.action_space = action_space
        self.epsilon = epsilon

    def __call__(self, agent, observation):
        agent.model.to(agent.device)
        qs = agent.model(observation.to(agent.device))
        if np.random.uniform() < self.epsilon:
            return qs.argmax().item()
        return np.random.choice(self.action_space)


class GreedyValueSelection:
    """
    Choosing the best possible option, necessary for evaluation
    """
    def __call__(self, agent, observation):
        qs = agent.model(observation.to(agent.device))
        return qs.argmax().item()


class ReinforcementLearner(Learner):
    def __init__(self,
                 model,
                 optimizer,
                 crit,
                 memory,
                 env,
                 memory_updater,
                 action_selector,
                 gamma,
                 grad_clip=None,
                 load_checkpoint=False,
                 name='',
                 callbacks=None,
                 dump_path='./tmp',
                 device='cpu'
                 ):
        """
        Abstract class for Reinforcement Learners

        Args:
            model:              neural network
            optimizer:          optimizer
            crit:               loss function
            memory:             memory to store and load the memory
            env:                environment to interact with
            memory_updater:     memory updater, also implementing the update policy
            action_selector:    action selection strategy
            gamma:              discount factor for reward over time
            grad_clip:          gradient clipping
            load_checkpoint:    bool, if a checkpoint should be loaded
            name:               name of the model
            callbacks:          list of callbacks to use
            dump_path:          dump path for the model and callbacks
            device:             device to run the model on
        """
        super().__init__(model,
                         optimizer,
                         crit,
                         train_loader=memory,
                         grad_clip=grad_clip,
                         load_checkpoint=load_checkpoint,
                         name=name,
                         callbacks=callbacks,
                         dump_path=dump_path,
                         device=device
                         )

        self.env = env
        self.memory_updater = memory_updater
        self.train_dict['rewards'] = []
        self.gamma = gamma
        self.chose_action = action_selector

    def fit_epoch(self, device, verbose=1):
        raise NotImplementedError

    def play_episode(self, render=False):
        raise NotImplementedError


class PolicyGradient(ReinforcementLearner):
    def __init__(self,
                 env,
                 model,
                 optimizer,
                 memory_updater,
                 n_samples,
                 batch_size,
                 crit=REINFORCELoss(),
                 action_selector=PolicyGradientActionSelection(),
                 memory_size=1000,
                 gamma=.95,
                 grad_clip=None,
                 load_checkpoint=False,
                 name='',
                 callbacks=None,
                 dump_path='./tmp',
                 device='cpu'):
        """
        Policy Gradient learner.

        Args:
            env:                environment to interact with
            model:              neural network
            optimizer:          optimizer
            memory_updater:     memory updater, also implementing the update policy
            n_samples:          number samples to sample for each update
            batch_size:         batch size for updates
            crit:               loss function
            action_selector:    action selection strategy
            memory_size:        memory size, storing passed memories
            gamma:              discount factor for rewards over time
            grad_clip:          gradient clipping
            load_checkpoint:    bool, if checkpoint should be loaded
            name:               name of the agent
            callbacks:          list of callbacks to use during training
            dump_path:          dump path for the model and the callbacks
            device:             device to run the model on
        """
        super().__init__(model=model,
                         optimizer=optimizer,
                         crit=crit,
                         env=env,
                         gamma=gamma,
                         memory=Memory(['log_prob', 'reward'],
                                       buffer_size=memory_size,
                                       n_samples=n_samples,
                                       gamma=gamma,
                                       batch_size=batch_size),
                         memory_updater=memory_updater,
                         action_selector=action_selector,
                         grad_clip=grad_clip,
                         load_checkpoint=load_checkpoint,
                         name=name,
                         callbacks=callbacks,
                         dump_path=dump_path,
                         device=device)

    def fit_epoch(self, device, verbose=1):
        """
        Train a single epoch.

        Args:
            device: device t-o run it on 'cpu' or 'cuda'
            verbose: verbosity of the learning

        Returns:
            current loss
        """
        self.memory_updater(self)
        self.model.train()
        self.model.to(device)

        losses = []

        for batch, (log_prob, reward) in tqdm(enumerate(self.train_loader)):
            log_prob, reward = log_prob.to(device), reward.to(device)
            loss = self.crit(log_prob, reward)
            self._backward(loss)
            losses += [loss.item()]
        loss = np.mean(losses)
        self.train_dict['train_losses'] += [loss]
        if verbose == 1:
            print(f'epoch: {self.train_dict["epochs_run"]}\t'
                  f'average reward: {np.mean(self.train_dict["rewards"]):.2f}\t'
                  f'latest average reward: {self.train_dict["avg_reward"][-1]:.2f}')
        return loss

    def play_episode(self, render=False):
        """
        Plays a single episode.
        This might need to be changed when using a non openAI gym environment.

        Args:
            render (bool): render environment

        Returns:
            episode reward
        """
        observation = self.env.reset().detach()
        episode_reward = 0
        step_counter = 0
        terminate = False
        episode_memory = Memory(['log_prob', 'reward'], gamma=self.gamma)

        while not terminate:
            step_counter += 1
            action, log_prob = self.chose_action(self, observation)
            new_observation, reward, done, _ = self.env.step(action)

            episode_reward += reward
            episode_memory.memorize((log_prob, torch.tensor(reward).float()), ['log_prob', 'reward'])
            observation = new_observation
            terminate = done or (self.env.max_episode_length is not None
                                 and step_counter >= self.env.max_episode_length)
            if render:
                self.env.render()
            # if done:
            #     break

        episode_memory.cumul_reward()
        self.train_loader.memorize(episode_memory, episode_memory.memory_cell_names)
        self.train_dict['rewards'] = self.train_dict.get('rewards', []) + [episode_reward]

        if episode_reward > self.train_dict.get('best_performance', -np.inf):
            self.train_dict['best_performance'] = episode_reward

        return episode_reward

    def _backward(self, loss):
        """
        Backward pass for the model, also performing a grad clip if defined for the learner.

        Args:
            loss: loss the backward pass is based on

        Returns:
            None

        """
        self.optimizer.zero_grad()
        loss.clone().backward(retain_graph=True)
        if self.grad_clip is not None:
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.grad_clip)
        self.optimizer.step()


class QLearner(ReinforcementLearner):
    def __init__(self,
                 model,
                 optimizer,
                 crit,
                 env,
                 memory_updater,
                 action_selector,
                 gamma,
                 alpha,
                 memory_size,
                 n_samples,
                 batch_size,
                 grad_clip=None,
                 load_checkpoint=False,
                 name='q_learner',
                 callbacks=[],
                 dump_path='./tmp',
                 device='cpu'):
        super().__init__(model=model,
                         optimizer=optimizer,
                         crit=crit,
                         env=env,
                         gamma=gamma,
                         memory=Memory(['action', 'state', 'reward', 'new_state'],
                                       buffer_size=memory_size,
                                       n_samples=n_samples,
                                       gamma=gamma,
                                       batch_size=batch_size),
                         memory_updater=memory_updater,
                         action_selector=action_selector,
                         grad_clip=grad_clip,
                         load_checkpoint=load_checkpoint,
                         name=name,
                         callbacks=callbacks,
                         dump_path=dump_path,
                         device=device)
        self.train_dict['train_losses'] = []
        self.alpha = alpha

    def fit_epoch(self, device, verbose=1):
        self.memory_updater(self)
        self.model.train()
        self.model.to(device)

        losses = []

        for batch, (action, state, reward, new_state) in tqdm(enumerate(self.train_loader)):
            prediction = self.model(state.squeeze(1))
            with torch.no_grad():
                self.model.eval()
                max_next = self.model(new_state.squeeze(1)).max(dim=1)[0]
            target = prediction.clone().detach()

            for t, a, r, m in zip(target, action, reward, max_next):
                # @todo this is ugly as fuck, there has to be a more efficient way
                t[a.item()] += self.alpha * (r + self.gamma * m - t[a.item()])
            # print(prediction.squeeze(1) - target.squeeze(1))
            # raise NotImplementedError
            loss = self.crit(prediction, target)
            self.train_dict['train_losses'] += [loss.item()]
            self._backward(loss)
            # print(f'weights: {self.model.fc1.weight.sum()}')

        if verbose == 1:
            print(f'epoch: {self.train_dict["epochs_run"]}\t'
                  f'average reward: {np.mean(self.train_dict["rewards"]):.2f}\t'
                  f'latest average reward: {self.train_dict["avg_reward"][-1]:.2f}')
        return loss

    def play_episode(self, render=False):
        observation = self.env.reset().detach()
        episode_reward = 0
        step_counter = 0
        terminate = False
        episode_memory = Memory(['action', 'state', 'reward', 'new_state'], gamma=self.gamma)
        self.eval()

        while not terminate:
            step_counter += 1
            with torch.no_grad():
                action = self.chose_action(self, observation)
            new_observation, reward, done, _ = self.env.step(action)

            episode_reward += reward
            episode_memory.memorize((action,
                                     observation,
                                     torch.tensor(reward).float(),
                                     new_observation),
                                    ['action', 'state', 'reward', 'new_state'])
            observation = new_observation
            terminate = done or (self.env.max_episode_length is not None
                                 and step_counter >= self.env.max_episode_length)
            if render:
                self.env.render()
            if done:
                break
        self.train()
        self.train_loader.memorize(episode_memory, episode_memory.memory_cell_names)
        self.train_dict['rewards'] = self.train_dict.get('rewards', []) + [episode_reward]

        if episode_reward > self.train_dict.get('best_performance', -np.inf):
            self.train_dict['best_performance'] = episode_reward

        return episode_reward
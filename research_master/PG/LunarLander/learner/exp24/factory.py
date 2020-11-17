import torch
import pymatch.ReinforcementLearning.callback as rcb
import pymatch.DeepLearning.callback as cb
import pymatch.ReinforcementLearning.learner as rl

from pymatch.ReinforcementLearning.loss import REINFORCELoss
from pymatch.ReinforcementLearning.memory import MemoryUpdater
from pymatch.ReinforcementLearning.torch_gym import TorchGym


def factory(Model, model_args, env_args, optim_args, memory_args, learner_args, name):
    model = Model(**model_args)
    env = TorchGym(**env_args)
    optim = torch.optim.SGD(model.parameters(), **optim_args)
    crit = REINFORCELoss()
    memory_updater = MemoryUpdater(**memory_args)
    learner = rl.PolicyGradient(env=env,
                                model=model,
                                optimizer=optim,
                                memory_updater=memory_updater,
                                crit=crit,
                                action_selector=rl.PolicyGradientActionSelection(),
                                callbacks=[
                                    rcb.EnvironmentEvaluator(env=env, n_evaluations=10, frequency=1),
                                    cb.Checkpointer(),
                                    # rcb.AgentVisualizer(env=env, frequency=5),
                                    cb.MetricPlotter(frequency=1, metric='rewards', smoothing_window=100),
                                    cb.MetricPlotter(frequency=1, metric='train_losses', smoothing_window=100),
                                    cb.MetricPlotter(frequency=1, metric='avg_reward', smoothing_window=5),
                                    cb.MetricPlotter(frequency=5, metric='val_reward', x='val_epoch', smoothing_window=5),
                                ],
                                **learner_args
                                )
    return learner
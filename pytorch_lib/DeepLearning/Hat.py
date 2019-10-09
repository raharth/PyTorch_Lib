import torch


class Hat:

    def cover(self, y):
        raise NotImplementedError


class HatCord:

    def __init__(self, model, hat):
        self.model = model
        self.hat = hat

    def predict(self, X, device='cpu', learner_args={}):
        y = self.model.predict(X, device, **learner_args)
        y = self.hat.cover(y)
        return y


class DefaultClassHat(Hat):

    def __init__(self):
        """
        Adding a default class to a sigmoid output.

        """
        super(DefaultClassHat, self).__init__()

    def cover(self, y):
        y_def = torch.clamp(1 - y.sum(1), min=0., max=1.).view(-1,1)
        return torch.cat([y, y_def], dim=1)


class LabelHat(Hat):

    def __init__(self):
        """
        Predicts the a label from a probability distribution.

        """
        super(LabelHat, self).__init__()

    def cover(self, y):
        return y.max(dim=1)[1]


class MajorityHat(Hat):

    def __init__(self):
        super(MajorityHat, self).__init__()

    def cover(self, y):
        # @todo validate/test
        y_pred = []
        y_count = []

        label_hat = LabelHat()
        y = label_hat.cover(y)

        for y_vote in y.transpose(0, 1):
            val, count = torch.unique(y_vote, return_counts=True)
            y_pred += [val[count.argmax()].item()]
            y_count += [count[count.argmax()] / float(len(self.learners))]

        return torch.tensor(y_pred), torch.tensor(y_count)


class EnsembleHat(Hat):

    def __init__(self):
        super(EnsembleHat, self).__init__()

    def cover(self, y):
        return y.mean(dim=0), y.std(dim=0)

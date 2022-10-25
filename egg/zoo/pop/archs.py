# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import itertools
from typing import Optional, Union
from xmlrpc.client import Boolean

import timm
import numpy as np
import torch
import torch.nn as nn
import torchvision
from egg.core.interaction import LoggingStrategy
from egg.core.gs_wrappers import gumbel_softmax_sample
from egg.zoo.pop.scripts.analysis_tools.test_game import add_noise

from collections.abc import Mapping


def get_non_linearity(name):
    if name == "softmax":
        return nn.Softmax
    elif name == "sigmoid":
        return nn.Sigmoid


def get_model(name, pretrained, aux_logits=True):
    modules = {
        "resnet50": (torchvision.models.resnet50, {"pretrained": pretrained}),
        "resnet101": (torchvision.models.resnet101, {"pretrained": pretrained}),
        "resnet152": (torchvision.models.resnet152, {"pretrained": pretrained}),
        "inception": (
            torchvision.models.inception_v3,
            {"pretrained": pretrained, "aux_logits": aux_logits},
        ),
        "resnext": (torchvision.models.resnext50_32x4d, {"pretrained": pretrained}),
        "mobilenet": (
            torchvision.models.mobilenet_v3_large,
            {"pretrained": pretrained},
        ),
        "vgg11": (torchvision.models.vgg11, {"pretrained": pretrained}),
        "densenet": (torchvision.models.densenet161, {"pretrained": pretrained}),
        "vit": (
            timm.create_model,
            {"model_name": "vit_base_patch16_384", "pretrained": pretrained},
        ),
        "swin": (
            timm.create_model,
            {"model_name": "swin_base_patch4_window12_384", "pretrained": pretrained},
        ),
        "dino": (
            torch.hub.load,
            {
                "repo_or_dir": "facebookresearch/dino:main",
                "model": "dino_vits16",
                "verbose": False,
            },
        ),
        "twins_svt": (
            timm.create_model,
            {"model_name": "twins_svt_base", "pretrained": pretrained},
        ),
        "deit": (
            timm.create_model,
            {"model_name": "deit_base_patch16_384", "pretrained": pretrained},
        ),
        "xcit": (
            timm.create_model,
            {"model_name": "xcit_large_24_p8_384_dist", "pretrained": pretrained},
        ),
    }

    if name not in modules:
        raise KeyError(f"{name} is not currently supported.")
    return modules[name][0](**modules[name][1])


def initialize_vision_module(
    name: str = "resnet50", pretrained: bool = False, aux_logits=True
):
    print("initialize module", name)
    model = get_model(name, pretrained, aux_logits)
    # TODO: instead of this I'd feel like using the dictionary structure further and including in_features

    if name in ["resnet50", "resnet101", "resnet152", "resnext"]:
        n_features = model.fc.in_features
        model.fc = nn.Identity()
    if name == "densenet":
        n_features = model.classifier.in_features
        model.classifier = nn.Identity()
    if name == "mobilenet":
        n_features = model.classifier[3].in_features
        model.classifier[3] = nn.Identity()

    elif name == "vgg11":
        n_features = model.classifier[6].in_features
        model.classifier[6] = nn.Identity()

    elif name == "inception":
        n_features = model.fc.in_features
        if model.AuxLogits is not None:
            model.AuxLogits.fc = nn.Identity()
        model.fc = nn.Identity()

    elif name in ["vit", "swin", "xcit", "twins_svt", "deit"]:
        n_features = model.head.in_features
        model.head = nn.Identity()

    elif name == "dino":
        n_features = 384  # ... could go and get that somehow instead of hardcoding ?
        # Dino is already chopped and does not require removal of classif layer

    if pretrained:
        for param in model.parameters():
            param.requires_grad = False
        if name == "inception":
            model.aux_logits = False
        model = (
            model.eval()
        )  # Mat : --> dropout blocked, as well as all other training dependant behaviors

    return model, n_features, name


class Sender(nn.Module):
    def __init__(
        self,
        vision_module: Union[nn.Module, str],
        input_dim: Optional[int],
        name: str = "resnet50",
        vocab_size: int = 2048,
    ):
        super(Sender, self).__init__()
        self.name = name
        self.init_vision_module(vision_module, input_dim)
        self.init_com_layer(input_dim, vocab_size)

    def train(self, mode: bool = True):
        r"""
        sets all in training mode EXCEPT vision module which is pre-trained and frozen
        """
        if not isinstance(mode, bool):
            raise ValueError("training mode is expected to be boolean")
        self.training = mode
        for module in self.children():
            if module != self.vision_module:
                module.train(mode)
        return self

    def init_vision_module(self, vision_module, input_dim):
        if isinstance(vision_module, nn.Module):
            self.vision_module = vision_module
            input_dim = input_dim
        elif isinstance(vision_module, str):
            self.vision_module, input_dim = initialize_vision_module(vision_module)
        else:
            raise RuntimeError("Unknown vision module for the Sender")

    def init_com_layer(self, input_dim, vocab_size):
        self.fc = nn.Sequential(
            nn.Linear(input_dim, vocab_size),
            nn.BatchNorm1d(vocab_size),
        )
        pass

    def forward(self, x, aux_input=None):
        vision_module_out = self.vision_module(x)
        # if not self.training: # this is commented because with pops of agents each vision module has a different size and interactions can't concat
        #     aux_input["resnet_output_sender"] = vision_module_out.detach()
        # elif self.name == "inception": # This is commented because incep is set not to have logits in the setting where models are pretrained
        #     vision_module_out = vision_module_out.logits

        return self.fc(vision_module_out)


class ContinuousSender(Sender):
    def __init__(
        self,
        vision_module: Union[nn.Module, str],
        input_dim: Optional[int],
        name: str = "resnet50",
        vocab_size: int = 2048,
        non_linearity: nn.Module = None,
        force_gumbel: Boolean = False,
        forced_gumbel_temperature=5,
        block_com_layer: bool = False,
    ):
        super(Sender, self).__init__()
        self.name = name
        self.init_vision_module(vision_module, input_dim)
        self.init_com_layer(
            input_dim, vocab_size, get_non_linearity(non_linearity), block_com_layer
        )
        self.force_gumbel = force_gumbel
        self.forced_gumbel_temperature = forced_gumbel_temperature

    def init_com_layer(
        self,
        input_dim,
        vocab_size,
        non_linearity: nn.Module = None,
        block_com_layer: bool = False,
    ):
        self.fc = (
            nn.Identity()
            if block_com_layer
            else nn.Sequential(
                nn.Linear(input_dim, vocab_size),
                nn.BatchNorm1d(vocab_size),
                non_linearity() if non_linearity is not None else nn.Identity(),
            )
        )
        pass

    def forward(self, x, aux_input=None):
        vision_module_out = self.vision_module(x)
        if self.force_gumbel:
            return gumbel_softmax_sample(
                self.fc(vision_module_out), temperature=self.forced_gumbel_temperature
            )
        else:
            return self.fc(vision_module_out)


class Receiver(nn.Module):
    def __init__(
        self,
        vision_module: Union[nn.Module, str],
        input_dim: int,
        name: str = "resnet50",
        hidden_dim: int = 2048,
        output_dim: int = 2048,
        temperature: float = 1.0,
        block_com_layer=False,
    ):

        super(Receiver, self).__init__()

        self.name = name

        if isinstance(vision_module, nn.Module):
            self.vision_module = vision_module
            input_dim = input_dim
        elif isinstance(vision_module, str):
            self.vision_module, input_dim = initialize_vision_module(vision_module)
        else:
            raise RuntimeError("Unknown vision module for the Receiver")
        self.fc = (
            nn.Identity()
            if block_com_layer
            else nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.BatchNorm1d(hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, output_dim, bias=False),
            )
        )
        self.temperature = temperature

    def train(self, mode: bool = True):
        """
        sets all in training mode EXCEPT vision module which is pre-trained and frozen
        """
        if not isinstance(mode, bool):
            raise ValueError("training mode is expected to be boolean")
        self.training = mode
        for module in self.children():
            if module != self.vision_module:
                module.train(mode)
        return self

    def forward(self, message, distractors, aux_input=None):
        vision_module_out = self.vision_module(distractors)
        # if self.name == "inception":
        #     vision_module_out = vision_module_out.logits
        distractors = self.fc(vision_module_out)

        similarity_scores = (
            torch.nn.functional.cosine_similarity(
                message.unsqueeze(1), distractors.unsqueeze(0), dim=2
            )
            / self.temperature
        )

        if not self.training:
            aux_input["receiver_message_embedding"] = message.detach()

        return similarity_scores


class AgentSampler(nn.Module):
    """Random sampler at training time, fullsweep sampler at test time."""

    def __init__(self, senders, receivers, losses, seed=1234):
        super().__init__()

        np.random.seed(seed)

        self.senders = nn.ModuleList(senders)
        self.receivers = nn.ModuleList(receivers)
        self.losses = list(losses)

        self.sender_lock_idx = 0
        self.receiver_lock_idx = 0
        self.iterator = self.reset_order()
        self.available_indexes = list(self.reset_order())

    def avoid_training_old(self):
        """
        all available senders and receivers are considered 'old', and will not be trained anymore
        Warning : After using this function, new senders and receivers must be added otherwise the sampler
        will not select any sender receiver pairs
        """
        self.sender_lock_idx = len(self.senders)
        self.receiver_lock_idx = len(self.receivers)

    def add_senders(self, new_senders):
        """
        Used to measure the ease of learning of a new agent
        adds a new sender to those available
        """
        self.senders += new_senders
        self.senders_order = list(range(len(self.senders)))
        self.iterator = self.reset_order()
        self.available_indexes = list(self.reset_order())

    def add_receivers(self, new_receivers):
        self.receivers += new_receivers
        self.receivers_order = list(range(len(self.receivers)))
        self.iterator = self.reset_order()
        self.available_indexes = list(self.reset_order())

    def reset_order(self):
        # old - new pairs and new - new pairs
        _iterator = itertools.product(
            list(range(len(self.senders))),
            list(range(self.receiver_lock_idx, len(self.receivers))),
            list(range(len(self.losses))),
        )

        # adding new-old pairs
        _chained_iterator = itertools.chain(
            _iterator,
            itertools.product(
                list(range(self.sender_lock_idx, len(self.senders))),
                list(range(self.receiver_lock_idx)),
                list(range(len(self.losses))),
            ),
        )
        # Old method, trains everyone
        # _chained_iterator = itertools.product(
        #     list(range(len(self.senders))),
        #     list(range(len(self.receivers))),
        #     list(range(len(self.losses))),
        # )
        return _chained_iterator

    def forward(self):
        if self.training:
            sender_idx, recv_idx, loss_idx = self.available_indexes[
                np.random.randint(0, len(self.available_indexes))
            ]
        else:
            try:
                sender_idx, recv_idx, loss_idx = next(self.iterator)
            except StopIteration:
                self.iterator = itertools.chain(self.available_indexes)
                sender_idx, recv_idx, loss_idx = next(self.iterator)

        return (
            self.senders[sender_idx],
            self.receivers[recv_idx],
            self.losses[loss_idx],
            (
                torch.Tensor([sender_idx]).int(),
                torch.Tensor([recv_idx]).int(),
                torch.Tensor([loss_idx]).int(),
            ),
        )


class Game(nn.Module):
    def __init__(
        self,
        train_logging_strategy: Optional[LoggingStrategy] = None,
        test_logging_strategy: Optional[LoggingStrategy] = None,
        noisy=None,
    ):
        super(Game, self).__init__()
        self.noisy = noisy
        self.train_logging_strategy = (
            LoggingStrategy()
            if train_logging_strategy is None
            else train_logging_strategy
        )
        self.test_logging_strategy = (
            LoggingStrategy()
            if test_logging_strategy is None
            else test_logging_strategy
        )

    def forward(
        self,
        sender,
        receiver,
        loss,
        sender_input,
        labels,
        receiver_input=None,
        aux_input=None,
    ):
        # if not self.training:
        # sender.to("cuda")  # Mat !! TODO : change this to common opts device
        # receiver.to("cuda")
        # sender_input = sender_input.to("cuda")
        # receiver_input = receiver_input.to("cuda")

        message = sender(sender_input, aux_input)
        receiver_output = receiver(
            message if self.noisy is None else add_noise(message, self.noisy),
            receiver_input,
            aux_input,
        )

        loss, aux_info = loss(
            sender_input,
            message,
            receiver_input,
            receiver_output,
            labels,
            aux_input,
        )

        logging_strategy = (
            self.train_logging_strategy if self.training else self.test_logging_strategy
        )
        interaction = logging_strategy.filtered_interaction(
            sender_input=sender_input,
            receiver_input=receiver_input,
            labels=labels,
            aux_input=aux_input,
            receiver_output=receiver_output,
            message=message.detach(),
            message_length=torch.ones(message[0].size(0)),
            aux=aux_info,
        )
        # if not self.training:
        # sender.to("cpu")
        # receiver.to("cpu")

        return loss.mean(), interaction, message


class PopulationGame(nn.Module):
    def __init__(
        self, game, agents_loss_sampler, device="cuda", aux_loss=None, aux_loss_weight=0
    ):
        super().__init__()
        # TODO : Mat : this should be in sync with distributed training
        self.device = device
        self.force_gpu_use = False

        self.game = game
        self.agents_loss_sampler = agents_loss_sampler

        # initialising aux_loss
        if aux_loss is not None:
            if aux_loss == "random":
                self.aux_loss = self.random_similarity_loss
            elif aux_loss == "best":
                self.aux_loss = self.best_similarity_loss
                self.best_sender_idx = None
                self.best_loss = 2**63 - 1
            elif aux_loss == "best_averaged":
                self.aux_loss = self.averaged_similarity_loss
                self.best_sender_idx = None
                self.best_loss = torch.Tensor(
                    [2**63 - 1 for _ in range(len(self.agents_loss_sampler.senders))]
                ).to(self.device)
                self.n_elemets = [
                    0 for _ in range(len(self.agents_loss_sampler.senders))
                ]
            elif aux_loss == "random_kl":
                self._kl = nn.KLDivLoss(reduction="batchmean")
                self.aux_loss = self.random_kl_loss
            elif aux_loss == "chosen":
                self.aux_loss = self.chosen_similarity_loss
                self.chosen_sender_idx = 1
            else:
                raise NotImplementedError
            self.aux_loss_weight = aux_loss_weight
        else:
            self.aux_loss_weight = 0

    def force_set_device(self, device):
        # to use when each agent needs to go back and forth on GPU, needs to be sent back to cpu in trainer after backprop and optim step
        # this does not work with actual core.trainers
        self.device = device
        self.force_gpu_use = True

    def random_similarity_loss(self, original_message, aux_input, batch, _loss):
        """
        takes a random agent, computes what message it would have given for the same input and calculates the similarity loss
        """
        aux_sender, _, _, aux_idxs = self.agents_loss_sampler()
        if self.force_gpu_use:
            aux_sender = aux_sender.to(self.device)
        aux_input["aux_sender_idx"] = aux_idxs[0]
        aux_loss = torch.nn.functional.cosine_similarity(
            original_message, aux_sender(batch, aux_input)
        )
        return aux_loss

    def chosen_similarity_loss(self, original_message, aux_input, batch, _loss):
        """
        takes a random agent, computes what message it would have given for the same input and calculates the similarity loss
        """
        aux_sender = self.agents_loss_sampler.senders[self.chosen_sender_idx]
        if self.force_gpu_use:
            aux_sender = aux_sender.to(self.device)
        aux_input["aux_sender_idx"] = torch.Tensor(self.chosen_sender_idx)
        aux_loss = torch.nn.functional.cosine_similarity(
            original_message, aux_sender(batch, aux_input)
        )
        return aux_loss

    def random_kl_loss(self, original_message, aux_input, batch, _loss):
        """
        takes a random agent, computes what message it would have given for the same input and calculates the similarity loss
        """
        aux_sender, _, _, aux_idxs = self.agents_loss_sampler()
        if self.force_gpu_use:
            aux_sender = aux_sender.to(self.device)
        aux_input["aux_sender_idx"] = aux_idxs[0]
        aux_loss = self._kl(original_message, aux_sender(batch, aux_input))
        return aux_loss

    def best_similarity_loss(self, original_message, aux_input, batch, loss):
        """
        takes the agent which has the best loss so far,
        computes what message it would have given for the same input and calculates the similarity loss
        """
        if loss < self.best_loss:
            self.best_loss = loss
            self.best_sender_idx = aux_input["sender_idx"]

        aux_sender = self.agents_loss_sampler.senders[self.best_sender_idx]
        if self.force_gpu_use:
            aux_sender = aux_sender.to(self.device)
        aux_input["aux_sender_idx"] = self.best_sender_idx
        aux_loss = torch.nn.functional.cosine_similarity(
            original_message, aux_sender(batch, aux_input)
        )
        return aux_loss

    def averaged_similarity_loss(self, original_message, aux_input, batch, loss):
        """
        takes the agent which has had the best loss so far averaged over time,
        computes what message it would have given for the same input and calculates the similarity loss
        """
        self.best_loss[aux_input["sender_idx"].item()] += (
            self.best_loss[aux_input["sender_idx"].item()] - loss
        ) / self.n_elemets[aux_input["sender_idx"]]
        _best_sender_idx = torch.argmin(self.best_loss)

        aux_sender = self.agents_loss_sampler.senders[_best_sender_idx]
        if self.force_gpu_use:
            aux_sender = aux_sender.to(self.device)
        aux_input["aux_sender_idx"] = _best_sender_idx.detach().to("cpu")
        aux_loss = torch.nn.functional.cosine_similarity(
            original_message, aux_sender(batch, aux_input)
        )
        return aux_loss

    def forward(self, *args, **kwargs):
        sender, receiver, loss, idxs = self.agents_loss_sampler()
        sender_idx, recv_idx, loss_idx = idxs

        # creating an aux_input
        args = list(args)
        args[-1] = {
            "sender_idx": sender_idx,
            "recv_idx": recv_idx,
            "loss_idx": loss_idx,
        }
        # add the aux_loss to the args

        if self.force_gpu_use:
            sender = sender.to(self.device)
            receiver = receiver.to(self.device)
            # if aux_loss, aux_sender is moved during auxiliary loss calculation
        mean_loss, interactions, message = self.game(
            sender, receiver, loss, *args, **kwargs
        )
        if self.aux_loss_weight > 0:
            mean_loss = (
                mean_loss
                + self.aux_loss_weight
                * self.aux_loss(message, args[-1], args[0], mean_loss).mean()
            )

        return mean_loss, interactions

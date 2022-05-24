# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import torch

# import json
# from pathlib import Path

import egg.core as core
from egg.core import ConsoleLogger

# from egg.core.callbacks import WandbLogger
from egg.zoo.pop.data import get_dataloader
from egg.zoo.pop.game_callbacks import (
    BestStatsTracker,
    DistributedSamplerEpochSetter,
    WandbLogger,
)
from egg.zoo.pop.games import build_game, build_second_game
from egg.zoo.pop.LARC import LARC
from egg.zoo.pop.utils import add_weight_decay, get_common_opts
import os


def main(params):
    opts = get_common_opts(params=params)
    assert (
        not opts.batch_size % 2
    ), f"Batch size must be multiple of 2. Found {opts.batch_size} instead"
    print(
        f"Running a distruted training is set to: {opts.distributed_context.is_distributed}. "
        f"World size is {opts.distributed_context.world_size}. "
        f"Using batch of size {opts.batch_size} on {opts.distributed_context.world_size} device(s)\n"
        f"Applying augmentations: {opts.use_augmentations} with image size: {opts.image_size}.\n"
    )
    if not opts.distributed_context.is_distributed and opts.pdb:
        breakpoint()

    val_loader, train_loader = get_dataloader(
        dataset_dir=opts.dataset_dir,
        dataset_name=opts.dataset_name,
        image_size=opts.image_size,
        batch_size=opts.batch_size,
        num_workers=opts.num_workers,
        is_distributed=opts.distributed_context.is_distributed,
        seed=111,  # seed for data is hard coded. Everyone is evaluated on the same data
        # Might cause problems for cross GPU version
        use_augmentations=opts.use_augmentations,
        return_original_image=opts.return_original_image,
        split_set=True,
    )

    game = (
        build_game(opts) if opts.base_checkpoint_path == "" else build_second_game(opts)
    )

    model_parameters = add_weight_decay(game, opts.weight_decay, skip_name="bn")

    optimizer = torch.optim.Adam(model_parameters, lr=opts.lr)
    optimizer_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=opts.n_epochs
    )

    if opts.use_larc:
        optimizer = LARC(optimizer, trust_coefficient=0.001, clip=False, eps=1e-8)

    callbacks = [
        ConsoleLogger(as_json=True, print_train_loss=True),
        BestStatsTracker(),
        WandbLogger(opts),
    ]

    if opts.distributed_context.is_distributed:
        callbacks.append(DistributedSamplerEpochSetter())

    trainer = core.Trainer(
        game=game,
        optimizer=optimizer,
        optimizer_scheduler=optimizer_scheduler,
        train_data=train_loader,
        validation_data=val_loader,
        callbacks=callbacks,
        device="cpu",  # individual senders and receivers are sent to gpu in the game's forward
    )
    trainer.train(n_epochs=opts.n_epochs)

    # Quick test -- now on hold as validation occurs before
    # data_args = {
    #     "image_size": opts.image_size,
    #     "batch_size": opts.batch_size,
    #     "dataset_name": opts.dataset_name,
    #     "num_workers": opts.num_workers,
    #     "use_augmentations": False,
    #     "is_distributed": opts.distributed_context.is_distributed,
    #     "seed": opts.random_seed,
    # }

    # i_test_loader = get_dataloader(training_set=False, **data_args)
    # _, i_test_interaction = trainer.eval(i_test_loader)
    # dump = dict((k, v.mean().item()) for k, v in i_test_interaction.aux.items())
    # dump.update(dict(mode="VALIDATION_I_TEST"))
    # print(json.dumps(dump), flush=True)

    # if opts.checkpoint_dir:
    #     output_path = Path(opts.checkpoint_dir)
    #     output_path.mkdir(exist_ok=True, parents=True)
    #     torch.save(i_test_interaction, output_path / "i_test_interaction")

    print("| FINISHED JOB")


if __name__ == "__main__":
    torch.autograd.set_detect_anomaly(True)
    import sys

    main(sys.argv[1:])

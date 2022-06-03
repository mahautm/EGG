# BEWARE cuda is an uncontrolled mess
# This script loads games that have been trained for communication, then runs and saves communication for all available agent pairs
from egg.zoo.pop.games import build_game
from egg.zoo.pop.utils import load_from_checkpoint

# import sys
import pathlib
import torch
from egg.core.batch import Batch
from egg.core.interaction import Interaction
from egg.zoo.pop.data import get_dataloader
from egg.zoo.pop.utils import get_common_opts


def main(params):
    """
    starting point if script is executed from submitit or slurm with normal EGG parameters
    TODO : allow simpler loading, from a path, or by searching for a few parameters
    """
    torch.autograd.set_detect_anomaly(True)
    opts = get_common_opts(params)
    build_and_test_game(opts, exp_name=None, dump_dir=opts.checkpoint_dir)


def path_to_parameters():
    # WIP... this is mainly convenience

    # open the yaml file, look into it and get ALL the parameters.
    # input them in the opts, instead of rebuilding the game (beware, if its a second game this might be tricky and require a bit of hacking)
    pass


#
def eval(sender, receiver, loss, game, data=None, aux_input=None, gs=True):
    """
    Taken from core.trainers.py and modified (removed loss logging and multi-gpu support)
    runs each batch as a forward pass through the game, returns the interactions that occured
    """
    interactions = torch.tensor([])
    n_batches = 0
    validation_data = data
    with torch.no_grad():
        for batch in validation_data:
            if not isinstance(batch, Batch):
                batch = Batch(*batch)
            aux_input["batch_number"] = n_batches
            batch = batch.to("cuda")
            _, interaction = game(
                sender,
                receiver,
                loss,
                batch[0],
                batch[1],
                batch[2],
                aux_input,
            )
            interaction = interaction.to("cpu")
            game.to("cpu")
            if gs:
                interaction.message = interaction.message.argmax(dim=-1)

            # for key in aux_input:
            #     interaction.aux_input[key] = [aux_input[key]] * 64
            # interaction.aux_input["batch_number"] = [n_batches] * 64
            interactions.append(interaction)
            n_batches += 1
    # print("DEBUG : ", n_batches)
    full_interaction = Interaction.from_iterable(interactions)
    return full_interaction


# Taken from core.callbacks.InteractionSaver and modified
def dump_interactions(
    logs: Interaction,
    exp_name: str = "interaction_file",
    dump_dir: str = "./interactions",
):
    """
    Used to save interactions in a specified directory
    """
    dump_dir = pathlib.Path(dump_dir)
    dump_dir.mkdir(exist_ok=True, parents=True)
    torch.save(logs, dump_dir / exp_name)


# buids a game using the usual pop parameters, perfroms evaluations, saves interactions
def build_and_test_game(opts, exp_name, dump_dir, device="cuda"):
    """
    From an existing game save interactions of each possible agent pair
    Each agent pairs plays on the whole validation set
    """

    pop_game = build_game(opts)
    load_from_checkpoint(pop_game, opts.base_checkpoint_path)
    # make everything go to evaluation mode (non-trainable, no training behaviour of any layers)
    for param in pop_game.parameters():
        param.requires_grad = False
    pop_game.eval()

    # get validation data
    val_loader, _ = get_dataloader(
        dataset_dir=opts.dataset_dir,
        dataset_name=opts.dataset_name,
        image_size=opts.image_size,
        batch_size=opts.batch_size,
        num_workers=opts.num_workers,
        is_distributed=opts.distributed_context.is_distributed,
        seed=111,  # same as hardcoded version used in experiments
        use_augmentations=opts.use_augmentations,
        return_original_image=opts.return_original_image,
        split_set=True,
    )
    # Instead of letting the population game use the agent sampler to select a different pair for every batch
    # We choose the pair and evaluate it on all batches
    interactions = []

    for (
        sender_idx,
        recv_idx,
        loss_idx,
    ) in pop_game.agents_loss_sampler.available_indexes:
        # run inference
        # I feel like this is sort of evil and if it was not python I would definently not get away with this sort of meddling with inner parameters from outside
        sender = pop_game.agents_loss_sampler.senders[sender_idx]
        receiver = pop_game.agents_loss_sampler.receivers[recv_idx]
        loss = pop_game.agents_loss_sampler.losses[loss_idx]
        aux_input = torch.tensor(
            {
                "sender_idx": sender_idx,
                "recv_idx": recv_idx,
                "loss_idx": loss_idx,
            }
        )
        # run evaluation, collect resulting interactions
        interactions.append(
            eval(
                sender.to(device),
                receiver.to(device),
                loss,
                pop_game.game,
                val_loader,
                aux_input,
            )
        )

    # save data
    dump_interactions(
        Interaction.from_iterable(interactions),
        exp_name if exp_name is not None else "interactions",
        dump_dir,
    )


# if __name__ == "__main__":
#     torch.autograd.set_detect_anomaly(True)
#     # quick temporary hack : write exp name and dump dir before all the options
#     opts = get_common_opts(params=sys.argv[3:])
#     build_and_test_game(opts, exp_name=sys.argv[1], dump_dir=sys.argv[2])

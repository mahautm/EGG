from egg.zoo.pop.scripts.acc_graphs import graph_collector, acc_graph


def ls_graph(
    baselines=True,
    verbose=False,
    save_path="/shared/mateo/logs/",
    graph_name="arch_graph",
    graph_title=None,
):
    """
    params
    ------
    arch_name : string, {'vgg11', 'vit', 'inception', 'resnet152'}
        which architecture's data will be used in the graph
    baselines : bool
        whether baselines are also to be plotted (for now, only the full population baseline is available)
    """

    # xmin, xmax, ymin, ymax = axis()
    # xmin, xmax, ymin, ymax = axis([xmin, xmax, ymin, ymax])

    # sender graph

    _xs, _ys, _labels = graph_collector(
        names=[
            "vision_model_names_senders",
            "vision_model_names_recvs",
            "additional_sender",
            "additional_receiver",
        ],
        values=[
            [["vgg11", "vit", "resnet152", "inception"]],
            [["vgg11", "vit", "resnet152", "inception"]],
        ],
        verbose=verbose,
        label_names=["additional_sender", "additional_sender"],
    )
    xs += _xs
    ys += _ys
    labels += _labels
    colours += ["g"] * len(_xs)  # one specific colour
    # linestyles += ["-"] * len(_xs)

    # plot all aquired data
    acc_graph(
        xs,
        ys,
        labels,
        save_path,
        verbose,
        name=graph_name,
        title=graph_title,
        legend_title="additional_s --> additional_r",
        colours=colours,
    )
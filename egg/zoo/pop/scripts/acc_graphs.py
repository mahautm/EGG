import matplotlib.pyplot as plt


def text_to_data():  # Mat going through console
    with open("d/alpha/em_2040_0_log.out") as f:
        lines = f.readlines()
        print(lines)
    pass


def get_validation_data():
    # model identifyer
    # is the validation directly printed in the logs ? if yes could we json bundle it all for easier access ?
    # access the validation data
    # access the number of epochs as x
    # plot with line indicating arrival time at chosen performance
    # Additionaly, give the arriving accuracy and the number of epochs to reach peak performance (to check validity of what will later be used.)
    x = [1, 2, 3, 4, 5, 6]
    y = [1, 5, 3, 5, 7, 8]

    plt.plot(x, y)
    plt.show()

    pass


text_to_data()
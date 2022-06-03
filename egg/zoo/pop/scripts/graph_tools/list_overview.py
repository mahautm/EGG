import glob
from egg.zoo.pop.scripts.graph_tools.acc_graphs import metadata_opener, extract_param, text_to_acc
# from base dir, extract a few params and 2 of the accs
# print 

def list_results(path="/shared/mateo/logs/continuous_hyp_2/",param_names=['vision_model_names_recvs','non_linearity','force_gumbel','vocab_size'], epoch_numbers=[1,25]):
    file_paths = glob.glob(path + "*.out")
    for file_path in file_paths:
        # we're looking for this :
        with open(file_path) as file:
            # lets get reading
            params = [extract_param(param, metadata_opener(file, 'nest')) for param in param_names]
            accuracies = [text_to_acc(file)[1][i] for i in epoch_numbers]
            print(accuracies, params)
            
                    
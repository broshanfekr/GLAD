import argparse, random
from expts_glad.create_GGM import create_MN_random
#from cvxopt import matrix, solvers, spmatrix
#from cvxopt import matrix, solvers, sparse, spdiag, spmatrix
#from cvxopt import spmatrix as matrix
import torch
import networkx as nx
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import numpy as np 
from expts_glad import metrics
import pprint
from sklearn.covariance import GraphicalLassoCV #GraphLassoCV
from sklearn.covariance import GraphicalLasso #GraphLasso
from scipy import linalg
import scipy

TRAIN = True #False# True

parser = argparse.ArgumentParser(description='Block co-ordinate descent implementation')
parser.add_argument('--K_train', type=int, default=1, #1000,
                    help='Num of training examples for a fixed D')
parser.add_argument('--K_valid', type=int, default=1, #1000,
                    help='Number of valid examples for a fixed D ')
parser.add_argument('--K_test', type=int, default=10, #1000,
                    help='Number of testing examples for a fixed D')
parser.add_argument('--M', type=int, default= 4, #1000, #500, #6,#500,
                    help='number of samples M, R^(NxM)')
parser.add_argument('--SAMPLE_BATCHES', type=int, default= 1, #1000, #500, #6,#500,
                    help='number of batches of sample size M')
parser.add_argument('--p_min', type=float, default=0.12,
                    help='the weights of values in true precision matrices')
parser.add_argument('--p_max', type=float, default=0.25,
                    help='the weights of values in true precision matrices')
parser.add_argument('--N', type=int, default= 5, #250, #3,#250,
                    help='Number of nodes N, R^(NxM)')
parser.add_argument('--MAX_EPOCH', type=int, default=50,
                    help='Number of epochs/iterations to run')
parser.add_argument('--w_min', type=float, default=0.12,
                    help='the weights of values in true precision matrices')
parser.add_argument('--w_max', type=float, default=0.25,
                    help='the weights of values in true precision matrices')
parser.add_argument('--prob', type=float, default=0.05,
                    help='sparsity = 2*prob: probability for the erdos-renyi true graph')
parser.add_argument('--SIGNS', type=int, default= 0, #250, #3,#250,
                    help='1: create random graphs with postive and negative signs')
parser.add_argument('--rho', type=float, default=0.1, #0.03, #075,
                    help='penalty for regularization')
parser.add_argument('--MAX_DEG', type=int, default= 50, #250, #3,#250,
                    help='Max degree of the random erdos-renyi graph')
parser.add_argument('--theta_init_offset', type=float, default=0.1, #0.03, #075,
                    help='offset for setting the diagonal init theta')
parser.add_argument('--cp', type=float, default=0.2,
                    help='penalty for cholesky decomposition')
parser.add_argument('--USE_CUDA_FLAG', type=int, default=1,
                    help='USE GPU if = 1')
parser.add_argument('--varyS', type=int, default=0,
                    help='experiment to do varyS')
parser.add_argument('--EDGE_RECOVERY', type=int, default=1,
                    help='experiment to get the probability of success plots')
parser.add_argument('--graph_type', type=str, default='random_maxd',
                    help='grid/chain/star')

#args = parser.parse_args()
args, unknown = parser.parse_known_args()

# Global Variables
USE_CUDA = False
if args.USE_CUDA_FLAG == 1:
    USE_CUDA = True


def convert_to_torch(data, TESTING_FLAG=False):# convert from numpy to torch variable 
    if USE_CUDA == False:
        data = torch.from_numpy(data.astype(np.float, copy=False)).type(torch.FloatTensor)
    else: # On GPU
        if TESTING_FLAG == False:
            data = torch.from_numpy(data.astype(np.float, copy=False)).type(torch.FloatTensor).cuda()
        else: # testing phase, no need to store the data on the GPU
            data = torch.from_numpy(data.astype(np.float, copy=False)).type(torch.FloatTensor).cuda()
            data.requires_grad = False
    return data


def old_prepare_data_helper(graphs):
    theta, s = [], [] # precision_mat, samples covariance mat
    for g_num in graphs:
        precision_mat, data = graphs[g_num] # data = M x N
        theta.append(precision_mat)
        s.append(np.matmul(data.T, data)/(args.M))
    theta = np.array(theta)
    s     = np.array(s)
    return [theta, s]


def prepare_data_helper(graphs):# block coordinate descent 
    theta, X = [], [] # precision_mat, samples covariance mat
    for g_num in graphs:
        precision_mat, data = graphs[g_num] # data = M x N
        theta.append(precision_mat)
        X.append(data)
    theta = np.array(theta)
    X     = np.array(X)
    return [theta, X]


def prepare_data(mn):
    train_data = prepare_data_helper(mn.train_graphs)
#    valid_data = prepare_data_helper(mn.valid_graphs)
    test_data  = prepare_data_helper(mn.test_graphs)
    return train_data, test_data


def get_theta_pred(ll):
    return torch.matmul(ll, ll.transpose(-1, -2))

def my_cholesky(A, offset=args.cp): # formula taken from wiki
    off = convert_to_torch(np.array(offset), TESTING_FLAG=True)
    L = torch.zeros(args.N, args.N)
    if USE_CUDA:
        L = L.cuda()
    for i, (Ai, Li) in enumerate(zip(A, L)):# row wise i
        for j, Lj in enumerate(L[:i+1]): # for all rows j above i
            s = torch.sum(torch.Tensor([Li[k] * Lj[k] for k in range(j)]))
            if USE_CUDA:
                s = s.cuda()
#            print('e11: ', Ai[i], s, off)
#            Li[j] = torch.sqrt(torch.max(Ai[i] - s, off)) if (i==j) else (1.0/ Lj[j] * (Ai[j]-s))
            Li[j] = torch.sqrt(Ai[i] - s) if (i==j) else (1.0/ Lj[j] * (Ai[j]-s))
    return L 

def batch_cholesky(Ab): # TODO: Inplace?
    L = torch.zeros(Ab.shape)
    if USE_CUDA:
        L = L.cuda()
    for i, A in enumerate(Ab):
#        print('A = ', A)
        L[i, :, :] = my_cholesky(A)
    return L


def logdet_eig(A):
    return torch.sum(torch.log(torch.eig(A)[0][:, 0]))

def logdet_cholesky(A):
    ll = torch.cholesky(A)
    return 2*torch.sum(torch.log(ll.diag()))
    
def logdet_mycholesky(A):
    ll = my_cholesky(A)
    return 2*torch.sum(torch.log(ll.diag()))

def logdet_torch(A):
    #return torch.log(torch.det(A))
    return torch.logdet(A)

def get_logdet(A):
#    print('check_logdet: ', logdet_eig(A), logdet_cholesky(A), logdet_mycholesky(A), logdet_torch(A))
#    return logdet_eig(A)
#    return logdet_cholesky(A)
#    return logdet_mycholesky(A)
    return logdet_torch(A)

def get_duality_gap(theta, S):
    rho = torch.Tensor([args.rho])
    if USE_CUDA:
        rho = rho.cuda()
    #print('checkk: ', torch.inverse(theta), theta)
    U = torch.min(torch.max(torch.inverse(theta) - S, -1*rho), rho)
    t1 = -1*get_logdet(S+U) - args.N # term1 
    t2 = -1*get_logdet(theta)
    t3 = torch.trace(torch.matmul(S, theta))
    #t4 = args.rho*torch.max(torch.sum(torch.abs(theta), 0)) # L1 norm of mat is max abs column sum
    t4 = args.rho*torch.sum(torch.abs(theta)) # L1 norm of mat 
#    print('DUALITY: ', t1, t2, t3, t4)
    return t1+t2+t3+t4

def batch_duality_gap(Ab, Sb):
    v = 0
    for i, (A, S) in enumerate(zip(Ab, Sb)):
        v += get_duality_gap(A, S)
    return v


def get_step_size(theta_pred, theta_prev):
    num = torch.trace(torch.matmul(theta_pred-theta_prev, theta_pred-theta_prev))
    den = torch.trace(torch.matmul(theta_pred-theta_prev, torch.inverse(theta_prev)-torch.inverse(theta_pred)))
    step_size = num/den
#    print('STEP size: ', num, den, step_size)
    return step_size

def batch_step_size(theta_predb, theta_prevb):
    s = 0
    for i, (theta_pred, theta_prev) in enumerate(zip(theta_predb, theta_prevb)):
        s += get_step_size(theta_pred, theta_prev)
    return s/len(theta_predb)

def get_frobenius_norm(A):
    return torch.sum(A**2) 
    #return torch.mean(torch.sum(A**2, (1,2))) 

def eta(X, step_size):
    zero = torch.Tensor([0])#.type(self.dtype)
    if USE_CUDA == True:
        zero = zero.cuda()
    return torch.sign(X)*torch.max(zero, torch.abs(X)-step_size*args.rho)

def is_PSD(theta): # check PSD
    return torch.all(torch.eig(theta)[0][:, 0] > 0)

def get_f_theta(theta, S):
    t1 = -1*get_logdet(theta)
#    print('err: ', S, theta)
    t2 = torch.trace(torch.matmul(S, theta))
    return t1 + t2

def quad_approx(theta, prev_theta, S, step_size):
    f_theta = get_f_theta(theta, S)
    qt1 = get_f_theta(prev_theta, S) # term 1 of Q
    qt2 = torch.trace(torch.matmul(theta - prev_theta, S - torch.inverse(prev_theta)))
    qt3 = 1/(2*step_size) * torch.sum((theta - prev_theta)**2)# get_frobenius_norm
    Q_eta = qt1+qt2+qt3
#    print('QUAD approx: ', f_theta, Q_eta)
    #return f_theta <= Q_eta
    #return f_theta <= Q_eta or torch.abs(torch.abs(f_theta) - torch.abs(Q_eta)) <= 0.01 * torch.abs(f_theta) # difference within 1%  
    return f_theta <= Q_eta or torch.abs(torch.abs(f_theta) - torch.abs(Q_eta)) <= 0.01 * torch.abs(f_theta) # difference within 1%  
    
def check_conditions(theta, prev_theta, S, step_size):
    t1 = is_PSD(theta)
    if t1 == 0:
        return t1
    else:
        t2 = quad_approx(theta, prev_theta, S, step_size)
#        print('COND_check: ', t1, t2)
        return t2
#    ans = (t1==1) & (t2==1)
#    print('COND_check: ', t1, t2)
#    return ans   



def get_convergence_loss(theta_pred, theta_true):
    num = get_frobenius_norm(theta_pred - theta_true)
    den = get_frobenius_norm(theta_true)
    if USE_CUDA: 
        return 10*torch.log10(num/den).data.cpu().numpy() 
    else:
        return 10*torch.log10(num/den).data.numpy() 

def get_obj_val(theta, S):
    t1 = 0.5*get_f_theta(theta, S) 
    t2 = args.rho*torch.sum(torch.abs(theta))
#    print('t1, t2', t1, t2)
    if USE_CUDA:
        return (t1+t2).data.cpu().numpy()
    else:
        return (t1+t2).data.numpy()


def block_glasso(data, eps=1e-8, COLLECT=True):
    criterion = nn.MSELoss()# input, target
    theta_true, X = data

    # #############################################################################
    # Estimate the covariance 
    print('Using the lars method')
    S = np.dot(X.T, X) / args.M
#    model = GraphicalLassoCV(cv=2, alphas=5, n_refinements=5, tol=1e-6, 
#                              max_iter=100, mode='lars', n_jobs=-1)
    model = GraphicalLasso(alpha=args.rho, mode='lars', tol=1e-7, enet_tol=1e-6, 
               max_iter=args.MAX_EPOCH, verbose=True, assume_centered=True)
#    model = GraphLasso(alpha=args.rho, mode='lars', tol=1e-8, enet_tol=1e-6, 
#               max_iter=100, verbose=False, assume_centered=False)

#    print('Using the cd method')
#    model = GraphicalLassoCV(cv=2, alphas=5, n_refinements=5, tol=1e-6, 
#                              max_iter=100, mode='cd', n_jobs=-1)
    model.fit(X)
    cov_ = model.covariance_
    theta_pred = model.precision_
    # #############################################################################
    fdr, tpr, fpr, shd, nnz, nnz_true, ps = metrics.report_metrics(theta_true, theta_pred)
    cond_theta_pred, cond_theta_true = np.linalg.cond(theta_pred), np.linalg.cond(theta_true)
    num_itr = model.n_iter_
    rho_obtained = args.rho # the L1 penalty parameter
    print('Accuracy metrics: fdr ',fdr,  ' tpr ', tpr, ' fpr ', fpr, ' shd ', shd, ' nnz ', nnz, ' nnz_true ', nnz_true, ' sign_match ', ps, ' pred_cond ', cond_theta_pred, ' true_cond ', cond_theta_true, 'total itr: ', num_itr, ' penalty_rho: ', rho_obtained)

    # Getting the NMSE and objective value
    # results of convergence
    res_conv = []
    
    theta_true = convert_to_torch(theta_true, TESTING_FLAG=True)
    theta_pred = convert_to_torch(theta_pred, TESTING_FLAG=True)
    S = convert_to_torch(S, TESTING_FLAG=True)
    obj_true = get_obj_val(theta_true, S)
    if COLLECT:
        theta_pred_diag = torch.diag_embed(torch.diagonal(theta_pred, offset=0, dim1=-2, dim2=-1))
        theta_true_diag = torch.diag_embed(torch.diagonal(theta_true, offset=0, dim1=-2, dim2=-1))
        cv_loss, cv_loss_off_diag, obj_pred = get_convergence_loss(theta_pred, theta_true), get_convergence_loss(theta_pred-theta_pred_diag, theta_true-theta_true_diag), get_obj_val(theta_pred, S)
        res_conv.append([cv_loss, obj_pred, obj_true, cv_loss_off_diag])

    return [fdr, tpr, fpr, shd, nnz, nnz_true, ps, cond_theta_pred, cond_theta_true, num_itr, rho_obtained], res_conv # result of convergence

def my_isPSD(A, tol=1e-7):
    E,V = scipy.linalg.eigh(A)
    print('min_eig = ', np.min(E) , 'max_eig = ', np.max(E), ' min_diag = ', np.min(np.diag(A)))
    return np.all(E > -tol)

def check_sym(a, tol=1e-7):
    print('is PSD: ', my_isPSD(a))
    return np.allclose(a, a.T, atol=tol)

def main():
    print('creating the graph data')
    # M = Samples, N = features :  out_data = M x N
    #mn = create_MN(args.K_train, args.M, args.N, args.prob, args.K_valid, args.K_test)
    
    
    if args.EDGE_RECOVERY == 1:
        #mn = create_MN_edge_recovery(args.K_train, args.M, args.N, args.graph_type, args.SAMPLE_BATCHES, args.w, args.K_test)
#        mn = create_MN_vary_w(args.K_train, args.M, args.N, args.graph_type, args.SAMPLE_BATCHES, [args.w_min, args.w_max], args.K_test, args.K_valid)
#        mn = create_MN_vary_w(args.K_train, args.M, args.N, args.graph_type, args.SAMPLE_BATCHES, [args.w_min, args.w_max], args.K_test, [args.prob, args.MAX_DEG, args.SIGNS], args.K_valid)
        mn = create_MN_random(args.K_train, args.M, args.N, args.graph_type, args.SAMPLE_BATCHES, [args.p_min, args.p_max], args.K_test, args.K_valid)
    elif args.varyS == 1:
        mn = create_MN_varyS(args.K_train, args.M, args.N, [0.03, 0.1, 0.2], args.K_valid, args.K_test)
    else: # create graphs with varying sparsity
        mn = create_MN(args.K_train, args.M, args.N, args.prob, args.K_valid, args.K_test)
    if TRAIN == True:
        train_data, test_data = prepare_data(mn)
#        print('check: ', train_data[0].shape, train_data[1].shape)
#        gista_glasso(train_data) 

    # Collect the results for test_data
    res_str = [] # structure learning metrics  
    res_conv_loss = {}
    print('****Test Data****')
#    true_theta = test_data[0][0]
    skipped_graphs = 0
    for i, data in enumerate(zip(test_data[0], test_data[1])):
        print('test graph ', i)#, true_theta, data)

        str_metric, res_conv_loss[i] = block_glasso(data)
        res_str.append(str_metric)
    print('Optimization done, running analysis: total skipped graphs = ', skipped_graphs)
    # get the average results for analysis
    res_mean = np.mean(np.array(res_str), 0)
    res_std  = np.std(np.array(res_str), 0)
    res_mean = ["%.3f" %x for x in res_mean]
    res_std  = ["%.3f" %x for x in res_std]
    print('fdr, tpr, fpr, shd, nnz, nnz_true, ps, np.linalg.cond(theta_pred), np.linalg.cond(theta_true), total_itr, rho_penalty')
    print(*sum(list(map(list, zip(res_mean, res_std))), []), sep=', ')
    # print the convergence loss analysis
    print('ITR, conv_loss, obj_val_pred, obj_val_true, conv_loss_off_diag')
    res_conv = []
    early_terminate = {} 
    for itr in range(args.MAX_EPOCH):
        res_conv = []
        early_terminate[itr] = set()
        for k, v in res_conv_loss.items():
            if itr >= len(v):
                early_terminate[itr].add(k)
                #print('Some test graphs already converged at itr =',k, itr)
                continue
            res_conv.append(v[itr])
        res_conv = np.array(res_conv)
        if len(res_conv) == 0:
            print('all graphs converged by iteration = ', itr)
            break

        mean_vec = ["%.3f" %x for x in np.mean(res_conv, 0)]
        std_vec = ["%.3f" %x for x in np.std(res_conv, 0)]
        print(itr, *sum(list(map(list, zip(mean_vec, std_vec))), []), sep=', ')
    print('early optimization details')
    e=0
    for k, v in early_terminate.items():
        if len(v) > e :
            print('itr ', k, '# of early terminated graphs ', len(v))
            e = len(v)  
#    pprint.pprint(early_terminate)
    return 

if __name__=="__main__":
    main()

from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, log_loss, mean_squared_error
from scipy.sparse import coo_matrix, load_npz, save_npz
from tensorflow.python import debug as tf_debug
from collections import Counter, defaultdict
from datetime import datetime
import tensorflow as tf
import tensorflow.distributions as tfd
import tensorflow_probability as tfp
wtfd = tfp.distributions
import argparse
import os.path
import getpass
import pandas as pd
import numpy as np
import yaml
import json
import time
import sys


DESCRIPTION = 'Not yet global_bias variational approximation'

parser = argparse.ArgumentParser(description='Run VFM')
parser.add_argument('data', type=str, nargs='?', default='fraction')
parser.add_argument('--degenerate', type=bool, nargs='?', const=True, default=False)
parser.add_argument('--sparse', type=bool, nargs='?', const=True, default=False)
parser.add_argument('--regression', type=bool, nargs='?', const=True, default=False)
parser.add_argument('--classification', type=bool, nargs='?', const=True, default=False)

parser.add_argument('--epochs', type=int, nargs='?', default=500)
parser.add_argument('--d', type=int, nargs='?', default=20)
parser.add_argument('--gamma', type=float, nargs='?', default=0.01)
parser.add_argument('--sigma2', type=float, nargs='?', default=0.2)
parser.add_argument('--nb_batches', type=int, nargs='?', default=1000)
options = parser.parse_args()


if getpass.getuser() == 'jj':
    PATH = '/home/jj'
else:
    PATH = '/Users/jilljenn/code'

DATA = options.data
print('Data is', DATA)
VERBOSE = False

# Load data
if DATA in {'mangaki', 'movie1M', 'movie10M', 'movie100k'}:
    df = pd.read_csv(os.path.join(PATH, 'vae/data', DATA, 'data.csv'))
    try:
        with open(os.path.join(PATH, 'vae/data', DATA, 'config.yml')) as f:
            config = yaml.load(f)
            nb_users = config['nb_users']
            nb_items = config['nb_items']
    except IOError:
        nb_users = 1 + df['user'].max()
        nb_items = 1 + df['item'].max()
    df['item'] += nb_users
    print(df.head())
else:  # Default is Fraction
    ratings = np.load('fraction.npy')
    print('Fraction data loaded')

    nb_users, nb_items = ratings.shape
    print(nb_users, 'users', nb_items, 'items')
    entries = []
    for i in range(nb_users):
        for j in range(nb_items):
            entries.append([i, nb_users + j, ratings[i][j]])  # FM format
    df = pd.DataFrame(entries, columns=('user', 'item', 'outcome'))
    # X_train = np.array(X_train)

# Is it classification or regression?
if options.regression or 'rating' in df:
    is_regression = True
    is_classification = False
elif options.classification or 'outcome' in df:
    is_classification = True
    is_regression = False

nb_entries = len(df)

# Build sparse features
rows = np.arange(nb_entries).repeat(2)
cols = np.array(df[['user', 'item']]).flatten()
data = np.ones(2 * nb_entries)
X_fm = coo_matrix((data, (rows, cols)), shape=(nb_entries, nb_users + nb_items)).tocsr()
if is_regression:
    y_fm = np.array(df['rating'])
else:
    y_fm = np.array(df['outcome']).astype(np.float32)
save_npz(os.path.join(PATH, 'vae/data', DATA, 'X_fm.npz'), X_fm)
np.save(os.path.join(PATH, 'vae/data', DATA, 'y_fm.npy'), y_fm)

i_train, i_test = train_test_split(list(range(nb_entries)), test_size=0.2)
# i_train, i_val = train_test_split(i_trainval, test_size=0.2)
train = df.iloc[i_train]
# valid = df.iloc[i_val]
test = df.iloc[i_test]
print(train.head())

X_fm_train = X_fm[i_train]
X_fm_test = X_fm[i_test]

np_priors = np.zeros(nb_users + nb_items)
for k, v in Counter(train['user']).items():
    np_priors[int(k)] = v
for k, v in Counter(train['item']).items():
    np_priors[int(k)] = v
print('minimax', np_priors.min(), np_priors.max())
print(np_priors[nb_users - 5:nb_users + 5])

X_train = np.array(train[['user', 'item']])
if is_regression:
    y_train = np.array(train['rating']).astype(np.float32)
else:
    y_train = np.array(train['outcome']).astype(np.float32)
# y_train_bin = np.array(train['outcome'])
nb_train_samples = len(X_train)
indices_train = np.column_stack((np.arange(nb_train_samples).repeat(2), X_train.flatten()))
X_train_sp_tf = tf.SparseTensorValue(indices_train, np.ones(2 * nb_train_samples), [nb_train_samples, nb_users + nb_items])

X_test = np.array(test[['user', 'item']])
if is_regression:
    y_test = np.array(test['rating']).astype(np.float32)
else:
    y_test = np.array(test['outcome']).astype(np.float32)
# y_test_bin = np.array(test['outcome'])
nb_test_samples = len(X_test)
indices_test = np.column_stack((np.arange(nb_test_samples).repeat(2), X_test.flatten()))
X_test_sp_tf = tf.SparseTensorValue(indices_test, np.ones(2 * nb_test_samples), [nb_test_samples, nb_users + nb_items])

nb_samples, _ = X_train.shape

# Config
print('Nb samples', nb_samples)
embedding_size = options.d
# batch_size = 1
# batch_size = 5
# batch_size = 128
nb_iters = options.nb_batches
batch_size = nb_samples // nb_iters  # All
print('Nb iters', nb_iters)

epochs = options.epochs
gamma = options.gamma  # gamma 0.001 works better for classification
sigma2 = options.sigma2  # 0.8

dt = time.time()
print('Start')

# Handle data
# X_data = tf.data.Dataset.from_tensor_slices(X_fm_train)
# y_data = tf.data.Dataset.from_tensor_slices(y_train)
# user_data = tf.data.Dataset.from_tensor_slices(X_train[:, 0])
# item_data = tf.data.Dataset.from_tensor_slices(X_train[:, 1])
# dataset = tf.data.Dataset.zip((X_data, y_data, user_data, item_data)).batch(batch_size)
# iterator = dataset.make_initializable_iterator()
# X_fm_batch, outcomes, user_batch, item_batch = iterator.get_next()

print('Stop', time.time() - dt)  # 16 seconds pour Movielens

# tf.enable_eager_execution()  # Debug, impossible with batches

global_bias = tf.get_variable('global_bias', shape=[], initializer=tf.truncated_normal_initializer(stddev=0.1))
users = tf.get_variable('entities', shape=[nb_users + nb_items, 2 * embedding_size], initializer=tf.truncated_normal_initializer(stddev=0.1))
bias = tf.get_variable('bias', shape=[nb_users + nb_items, 2], initializer=tf.truncated_normal_initializer(stddev=0.1))
priors = tf.constant(np_priors[:, None].repeat(embedding_size, axis=1), dtype=np.float32)

def make_mu():
    return tfd.Normal(loc=0., scale=1.)

def make_lambda():
    return tfd.Beta(1., 1.)

def make_embedding_prior():
    # return tfd.Normal(loc=[0.] * embedding_size, scale=[1.] * embedding_size)
    return wtfd.MultivariateNormalDiag(loc=[0.] * embedding_size, scale_diag=[1.] * embedding_size, name='emb_prior')

def make_embedding_prior2(mu0, lambda0):
    return wtfd.MultivariateNormalDiag(loc=[mu0] * embedding_size, scale_diag=[1/lambda0] * embedding_size)

def make_embedding_prior3(entity_batch):
    prior_prec_entity = tf.nn.embedding_lookup(priors, entity_batch, name='priors_prec')
    return wtfd.MultivariateNormalDiag(loc=[0.] * embedding_size, scale_diag=1/tf.sqrt(prior_prec_entity), name='strong_emb_prior')

def make_bias_prior():
    # return tfd.Normal(loc=0., scale=1.)
    return wtfd.Normal(loc=0., scale=1., name='bias_prior')

def make_bias_prior2(mu0, lambda0):
    # return tfd.Normal(loc=0., scale=1.)
    return tfd.Normal(loc=mu0, scale=1/lambda0)

def make_bias_prior3(entity_batch):
    prior_prec_entity = tf.nn.embedding_lookup(priors, entity_batch)
    return tfd.Normal(loc=0., scale=1/tf.sqrt(prior_prec_entity[:, 0]), name='strong_bias_prior')

def make_user_posterior(user_batch):
    feat_users = tf.nn.embedding_lookup(users, user_batch)
    prior_prec_entity = tf.nn.embedding_lookup(priors, user_batch, name='priors_prec')
    # return tfd.Normal(loc=feat_users[:, :embedding_size], scale=feat_users[:, embedding_size:])
    if options.degenerate:
        std_devs = tf.zeros(embedding_size)
    else:
        # 1/tf.sqrt(prior_prec_entity)  # More precise if more ratings
        # tf.ones(embedding_size)  # Too imprecise
        std_devs = tf.nn.softplus(feat_users[:, :embedding_size])
    return wtfd.MultivariateNormalDiag(loc=feat_users[:, embedding_size:], scale_diag=std_devs, name='emb_posterior')

def make_entity_bias(entity_batch):
    bias_batch = tf.nn.embedding_lookup(bias, entity_batch)
    prior_prec_entity = tf.nn.embedding_lookup(priors, entity_batch)
    if options.degenerate:
        std_dev = 0.
    else:
        # 1/tf.sqrt(prior_prec_entity[:, 0])  # More precise if more ratings, should be clipped
        # 1.  # Too imprecise
        std_dev = tf.nn.softplus(bias_batch[:, 1])
    return tfd.Normal(loc=bias_batch[:, 0], scale=std_dev, name='bias_posterior')

# def make_item_posterior(item_batch):
#     items = tf.get_variable('items', shape=[nb_items, 2 * embedding_size])
#     feat_items = tf.nn.embedding_lookup(items, item_batch)
#     return tfd.Normal(loc=feat_items[:embedding_size], scale=feat_items[embedding_size:])

user_batch = tf.placeholder(tf.int32, shape=[None], name='user_batch')
item_batch = tf.placeholder(tf.int32, shape=[None], name='item_batch')
X_fm_batch = tf.sparse_placeholder(tf.int32, shape=[None, nb_users + nb_items], name='sparse_batch')
outcomes = tf.placeholder(tf.float32, shape=[None], name='outcomes')

# mu0 = make_mu().sample()
# lambda0 = make_lambda().sample()

all_entities = tf.constant(np.arange(nb_users + nb_items))

if options.degenerate:
    emb_user_prior = make_embedding_prior()
    emb_item_prior = make_embedding_prior()
    bias_user_prior = make_bias_prior()
    bias_item_prior = make_bias_prior()
else:
    emb_user_prior = make_embedding_prior3(user_batch)
    emb_item_prior = make_embedding_prior3(item_batch)
    bias_user_prior = make_bias_prior3(user_batch)
    bias_item_prior = make_bias_prior3(item_batch)

# emb_user_prior = make_embedding_prior2(mu0, lambda0)
# emb_item_prior = make_embedding_prior2(mu0, lambda0)
# bias_user_prior = make_bias_prior2(mu0, lambda0)
# bias_item_prior = make_bias_prior2(mu0, lambda0)

q_user = make_user_posterior(user_batch)
q_item = make_user_posterior(item_batch)
q_user_bias = make_entity_bias(user_batch)
q_item_bias = make_entity_bias(item_batch)

q_entity = make_user_posterior(all_entities)
q_entity_bias = make_entity_bias(all_entities)
all_bias = q_entity_bias.sample()
all_feat = q_entity.sample()
# feat_users2 = tf.nn.embedding_lookup(all_feat, user_batch)
# feat_items2 = tf.nn.embedding_lookup(all_feat, item_batch)
# bias_users2 = tf.nn.embedding_lookup(all_bias, user_batch)
# bias_items2 = tf.nn.embedding_lookup(all_bias, item_batch)

# feat_users = emb_user_prior.sample()
# feat_items = emb_item_prior.sample()
# bias_users = bias_user_prior.sample(tf.shape(user_batch)[0])
# bias_items = bias_item_prior.sample()

feat_users = q_user.sample()
feat_items = q_item.sample()
bias_users = q_user_bias.sample()
bias_items = q_item_bias.sample()

user_rescale = tf.nn.embedding_lookup(priors, user_batch)[:, 0]
print('rescale', user_rescale)
item_rescale = tf.nn.embedding_lookup(priors, item_batch)[:, 0]
# print(prior.cdf(1.7))
# for _ in range(3):
#     print(prior.sample([2]))

# Predictions
def make_likelihood(feat_users, feat_items, bias_users, bias_items):
    logits = tf.reduce_sum(feat_users * feat_items, 1) + bias_users + bias_items
    return tfd.Bernoulli(logits)

def make_likelihood_reg(feat_users, feat_items, bias_users, bias_items):
    logits = global_bias + tf.reduce_sum(feat_users * feat_items, 1) + bias_users + bias_items
    return tfd.Normal(logits, scale=sigma2, name='pred')

def make_sparse_pred(x):
    x = tf.cast(x, tf.float32)
    x2 = x ** 2
    w = tf.reshape(bias[:, 0], (-1, 1))  # Otherwise tf.matmul is crying
    V = users[:, embedding_size:]
    V2 = V ** 2
    logits = (tf.squeeze(tf.matmul(x, w, a_is_sparse=True)) +
              0.5 * tf.reduce_sum(tf.matmul(x, V, a_is_sparse=True) ** 2 -
                                  tf.matmul(x2, V2, a_is_sparse=True), axis=1))
    return tfd.Bernoulli(logits)

def make_sparse_pred_reg(x):
    x = tf.cast(x, tf.float32)
    x2 = x# ** 2  # FIXME if x is 0/1 it's okay
    w = tf.reshape(all_bias, (-1, 1))
    # w = tf.reshape(bias[:, 0], (-1, 1))  # Otherwise tf.matmul is crying
    # V = users[:, embedding_size:]
    V = all_feat
    V2 = V ** 2
    logits = (tf.squeeze(tf.sparse_tensor_dense_matmul(x, w)) +
              0.5 * tf.reduce_sum(tf.sparse_tensor_dense_matmul(x, V) ** 2 -
                                  tf.sparse_tensor_dense_matmul(x2, V2), axis=1))
    return tfd.Normal(logits, scale=sigma2)

if is_classification:
    likelihood = make_likelihood(feat_users, feat_items, bias_users, bias_items)
else:
    likelihood = make_likelihood_reg(feat_users, feat_items, bias_users, bias_items)
sparse_pred = make_sparse_pred_reg(X_fm_batch)
pred2 = sparse_pred.mean()
# ll = make_likelihood(feat_users2, feat_items2, bias_users2, bias_items2)
pred = likelihood.mean()
# print(likelihood.log_prob([1, 0]))

# Check shapes
# print('likelihood', likelihood.log_prob(outcomes))
# print('prior', emb_user_prior.log_prob(feat_users))
# print('scaled prior', emb_user_prior.log_prob(feat_users) / user_rescale)
# print('posterior', q_user.log_prob(feat_users))
# print('bias prior', bias_user_prior.log_prob(bias_users))
# print('bias posterior', q_user_bias.log_prob(bias_users))

# sentinel = likelihood.log_prob(outcomes)
# sentinel = bias_prior.log_prob(bias_users)
# sentinel = tf.reduce_sum(ll.log_prob(outcomes))
# sentinel2 = tf.reduce_sum(likelihood.log_prob(outcomes))

# elbo = tf.reduce_mean(
#     user_rescale * item_rescale * likelihood.log_prob(outcomes) +
#     item_rescale * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
#                     emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
#     user_rescale * (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
#                     emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)))

# (nb_users + nb_items) / 2
if options.degenerate:
    # elbo = -(tf.reduce_sum((pred - outcomes) ** 2 / 2) +
    #          0.1 * tf.reduce_sum(tf.nn.l2_loss(bias_users) + tf.nn.l2_loss(bias_items) +
    #          tf.nn.l2_loss(feat_users) + tf.nn.l2_loss(feat_items)))
    elbo = tf.reduce_mean(
        len(X_train) * likelihood.log_prob(outcomes) +
        # len(X_train) * sparse_pred.log_prob(outcomes) +
        (nb_users + nb_items) * 2 * (bias_user_prior.log_prob(bias_users) +
                                     emb_user_prior.log_prob(feat_users) +
                                     bias_item_prior.log_prob(bias_items) +
                                     emb_user_prior.log_prob(feat_items)), name='elbo')
# / 2 : 1.27
# * 2 : 1.16
elif options.sparse:
    elbo = tf.reduce_mean(
        # len(X_train) * likelihood.log_prob(outcomes) +
        len(X_train) * sparse_pred.log_prob(outcomes) +
        len(X_train) * 1/user_rescale * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
                          emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
        len(X_train) * 1/item_rescale * (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
                          emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)), name='elbo')
else:
    # elbo = tf.reduce_mean(
    #     len(X_train) * likelihood.log_prob(outcomes) +
    #     # len(X_train) * sparse_pred.log_prob(outcomes) +
    #     (nb_users + nb_items) / 2 * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
    #                                  emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users) +
    #                                  bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
    #                                  emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)), name='elbo')

    elbo = tf.reduce_mean(
        len(X_train) * likelihood.log_prob(outcomes) +
        len(X_train) * 1/user_rescale * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
                          emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
        len(X_train) * 1/item_rescale * (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
                          emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)), name='elbo')

# sentinel0 = tf.reduce_mean(len(X_train) * likelihood.log_prob(outcomes))
sentinel = {
    'pred': pred[0],
    'outcome': outcomes[0],
    'll mean': likelihood.mean(),
    'll log prob': -likelihood.log_prob(outcomes)[0],
    's ll log prob': -tf.reduce_sum(likelihood.log_prob(outcomes)),
    's pred delta': tf.reduce_sum((pred - outcomes) ** 2 / 2 + np.log(2 * np.pi) / 2),
    'bias sample': bias_users[0],
    'bias log prob': -bias_user_prior.log_prob(bias_users)[0],
    'sum bias log prob': -tf.reduce_sum(bias_user_prior.log_prob(bias_users)),
    'bias mean': bias_user_prior.mean(),
    'bias delta': bias_users[0] ** 2 / 2 + np.log(2 * np.pi) / 2,
    'sum bias delta': tf.reduce_sum(bias_users ** 2 / 2 + np.log(2 * np.pi) / 2)
}
# sentinela = likelihood.mean()
# sentinelb = 
# sentinel0 = -tf.reduce_sum(likelihood.log_prob(outcomes)[0])
# sentinel1 = tf.reduce_sum((outcomes[0] - pred[0]) ** 2 - 0.5 * np.log(2 * np.pi))

# sentinel2 = 
# sentinel3 = tf.reduce_sum()

# elbo4 = (# len(X_train) * tf.reduce_mean(ll.log_prob(outcomes)) +
#         len(X_train) * sparse_pred.log_prob(outcomes) +
#         tf.reduce_sum(bias_user_prior.log_prob(all_bias) - q_entity_bias.log_prob(all_bias)) +
#         tf.reduce_sum(emb_user_prior.log_prob(all_feat) - q_entity.log_prob(all_feat)))
# elbo4 = tf.add(elbo4, 0, name='elbo4')

# elbo2 = tf.reduce_mean(
#     len(X_train) * likelihood.log_prob(outcomes) +
#                      (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
#                       emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
#                      (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
#                       emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)), name='elbo2')

optimizer = tf.train.AdamOptimizer(gamma)  # 0.001
# optimizer = tf.train.GradientDescentOptimizer(gamma)
infer_op = optimizer.minimize(-elbo)


metrics = {
    'train': defaultdict(list),
    'valid': defaultdict(list),
    'test': defaultdict(list)
}

def save_metrics(category, epoch, y_truth, y_pred):
    print('[%s] pred' % category, y_truth[:5], y_pred[:5])
    metrics[category]['epoch'].append(epoch)
    metrics[category]['acc'].append(np.mean(y_truth == np.round(y_pred)))
    metrics[category]['rmse'].append(mean_squared_error(y_truth, y_pred) ** 0.5)
    if is_classification:
        metrics[category]['auc'].append(roc_auc_score(y_truth, y_pred))
        metrics[category]['nll'].append(log_loss(y_truth, y_pred, eps=1e-6))
    print('[%s] ' % category + ' '.join('{:s}={:f}'.format(metric, metrics[category][metric][-1]) for metric in metrics[category]))

train_elbo = []
stopped_at = None
with tf.Session() as sess:
    # sess = tf_debug.LocalCLIDebugWrapperSession(sess)
    sess.run(tf.global_variables_initializer())
    train_writer = tf.summary.FileWriter('/tmp/test', sess.graph)

    for epoch in range(1, epochs + 1):
        # sess.run(iterator.initializer)
        # np.random.shuffle(X_train)
        # np.random.shuffle(X_fm_train)
        lbs = []
        # c = 0
        dt = time.time()
        for nb_iter in range(nb_iters):
            dt0 = time.time()
            batch_ids = np.random.randint(0, nb_train_samples, size=batch_size)
            # x_batch, y_batch = sess.run(next_element)
            X_batch = X_train[batch_ids]
            # print(X_batch.shape, batch_size)
            # X_batch = X_fm_train[t * batch_size:(t + 1) * batch_size]
            # y_batch = y_train[t * batch_size:(t + 1) * batch_size]
            # print(X_fm_train[batch_ids].shape)
            # print(type(X_fm_train[batch_ids]))
            # print(type(X_fm_train))
            # print(y_train[batch_ids].shape)

            indices = np.column_stack((np.arange(batch_size).repeat(2), X_batch.flatten()))

            _, lb = sess.run([infer_op, elbo], feed_dict={user_batch: X_batch[:, 0],
                                                          item_batch: X_batch[:, 1],
                                                          outcomes: y_train[batch_ids],
                                                          X_fm_batch: tf.SparseTensorValue(indices, np.ones(2 * batch_size), [batch_size, nb_users + nb_items])})

            if VERBOSE:
                values = sess.run([sentinel[key] for key in sentinel], feed_dict={user_batch: X_batch[:, 0],
                                                              item_batch: X_batch[:, 1],
                                                              outcomes: y_train[batch_ids],
                                                              X_fm_batch: tf.SparseTensorValue(indices, np.ones(2 * batch_size), [batch_size, nb_users + nb_items])})

                for key, val in zip(sentinel, values):
                    print(key, val)

            lbs.append(lb)
            if nb_iter == 0:
                dt1 = time.time()
                # print(nb_iters, 'times', dt1 - dt0, '=', nb_iters * (dt1 - dt0))
                time_per_batch = dt1 - dt0
        dt1 = time.time()
        # print('all', 'times', dt1 - dt, 'average', (dt1 - dt) / nb_iters)
        time_per_epoch1 = dt1 - dt

            #except tf.errors.OutOfRangeError:
        # print('train', X_test[0])
        # print('train fm', X_fm_test[0])
        if VERBOSE:
            train_pred, train_pred2 = sess.run([pred, pred2], feed_dict={user_batch: X_train[:, 0],
                                                                     item_batch: X_train[:, 1],
                                                                     X_fm_batch: X_train_sp_tf})
            save_metrics('train', epoch, y_train, train_pred)
            

        if VERBOSE or epoch == epochs or epoch % 10 == 0:
            test_pred, test_pred2 = sess.run([pred, pred2], feed_dict={user_batch: X_test[:, 0],
                                                                   item_batch: X_test[:, 1],
                                                                   X_fm_batch: X_test_sp_tf})
                                                                   # X_fm_batch: X_fm_test})

            save_metrics('test', epoch, y_test, test_pred)

        train_elbo.append(np.mean(lbs))
        print('{:.3f}s Epoch {}: Lower bound = {}'.format(
              time.time() - dt, epoch, np.mean(lbs) / nb_train_samples))
        time_per_epoch2 = time.time() - dt
        if len(train_elbo) > 5 and sorted(train_elbo[-5:], reverse=True) == train_elbo[-5:]:  # Train lower bound is decreasing
            print('Stop training')
            stopped_at = epoch
            break

    test_pred, test_pred2 = sess.run([pred, pred2], feed_dict={user_batch: X_test[:, 0],
                                                                       item_batch: X_test[:, 1],
                                                                       X_fm_batch: X_test_sp_tf})
                                                                       # X_fm_batch: X_fm_test})


    for metric in metrics['test']:
        final = metrics['test'][metric][-1]
        best = (np.max if metric in {'auc', 'acc'} else np.min)(metrics['test'][metric])
        metrics['final ' + metric] = final
        metrics['best ' + metric] = best
        print('[{:s}] final={:f} best={:f}'.format(metric, final, best))

filename = '{:s}-{:d}.txt'.format(DATA, int(round(time.time())))
with open('results/{:s}'.format(filename), 'w') as f:
    f.write(json.dumps({
        'description': DESCRIPTION,
        'date': datetime.now().isoformat(),
        'time': {
            'batch': time_per_batch,
            'epoch': time_per_epoch1,
            'epoch2': time_per_epoch2
        },
        'stopped': stopped_at,
        'args': vars(options),
        'metrics': metrics,
    }, indent=4))

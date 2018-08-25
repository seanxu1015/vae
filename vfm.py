from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, log_loss, mean_squared_error
from scipy.sparse import coo_matrix, load_npz, save_npz
import tensorflow as tf
import tensorflow.distributions as tfd
import tensorflow_probability as tfp
wtfd = tfp.distributions
from collections import Counter
import argparse
import os.path
import getpass
import pandas as pd
import numpy as np
import yaml
import time
import sys


parser = argparse.ArgumentParser(description='Run VFM')
parser.add_argument('data', type=str, nargs='?', default='movie100k')
options = parser.parse_args()


if getpass.getuser() == 'jj':
    PATH = '/home/jj'
else:
    PATH = '/Users/jilljenn/code'

DATA = 'movie100k'
DATA = options.data
# DATA = 'mangaki'
VERBOSE = False

# Load data
if DATA == 'mangaki':
    with open(os.path.join(PATH, 'vae/data/mangaki/config.yml')) as f:
        config = yaml.load(f)
        nb_users = config['nb_users']
        nb_items = config['nb_items']

    df = pd.read_csv(os.path.join(PATH, 'vae/data/mangaki/data.csv'))
    df['item'] += nb_users
    print(df.head())  
elif DATA == 'movie100k':
    with open(os.path.join(PATH, 'vae/data/movie100k/config.yml')) as f:
        config = yaml.load(f)
        nb_users = config['nb_users']
        nb_items = config['nb_items']

    train = pd.read_csv(os.path.join(PATH, 'vae/data/movie100k/movie100k_train.csv'))
    print(train.shape)
    test = pd.read_csv(os.path.join(PATH, 'vae/data/movie100k/movie100k_test.csv'))
    print(test.shape)
    df = pd.concat((train, test))
    df['item'] += nb_users  # FM format  # OMG I forgot this
    print(df.shape)
    print(df.head())
elif DATA == 'movie':
    with open(os.path.join(PATH, 'vae/data/movie100k/config.yml')) as f:
        config = yaml.load(f)
        nb_users = config['nb_users']
        nb_items = config['nb_items']
    df = pd.read_csv(os.path.join(PATH, 'vae/data/movie100k/data.csv'))
    df['item'] += nb_users  # FM format
    # X = np.array(df)
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

nb_entries = len(df)

# Build sparse features
rows = np.arange(nb_entries).repeat(2)
cols = np.array(df[['user', 'item']]).flatten()
data = np.ones(2 * nb_entries)
X_fm = coo_matrix((data, (rows, cols)), shape=(nb_entries, nb_users + nb_items)).tocsr()
try:
    y_fm = np.array(df['rating'])
except:
    y_fm = np.array(df['outcome']).astype(np.float32)
save_npz(os.path.join(PATH, 'vae/data', DATA, 'X_fm.npz'), X_fm)
np.save(os.path.join(PATH, 'vae/data', DATA, 'y_fm.npy'), y_fm)

i_train, i_test = train_test_split(list(range(nb_entries)), test_size=0.2)
train = df.iloc[i_train]
test = df.iloc[i_test]
print(train.head())

X_fm_train = X_fm[i_train]
print(X_fm_train[:5])
X_fm_test = X_fm[i_test]

np_priors = np.zeros(nb_users + nb_items)
for k, v in Counter(train['user']).items():
    np_priors[int(k)] = v
for k, v in Counter(train['item']).items():
    np_priors[int(k)] = v
print('minimax', np_priors.min(), np_priors.max())
print(np_priors[nb_users - 5:nb_users + 5])

X_train = np.array(train[['user', 'item']])
try:
    y_train = np.array(train['rating']).astype(np.float32)
except:
    y_train = np.array(train['outcome']).astype(np.float32)
# y_train_bin = np.array(train['outcome'])
nb_train_samples = len(X_train)

X_test = np.array(test[['user', 'item']])
try:
    y_test = np.array(test['rating']).astype(np.float32)
except:
    y_test = np.array(test['outcome']).astype(np.float32)
# y_test_bin = np.array(test['outcome'])
nb_test_samples = len(X_test)

nb_samples, _ = X_train.shape

# Config
print('Nb samples', nb_samples)
embedding_size = 20
# batch_size = 5
# batch_size = 128
# batch_size = nb_samples // 1000
# batch_size = nb_samples // 100
# batch_size = nb_samples // 20
batch_size = nb_samples  # All
iters = nb_samples // batch_size
print('Nb iters', iters)
epochs = 500
gamma = 0.1  # gamma 0.001 works better for classification
sigma = 0.8

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

users = tf.get_variable('entities', shape=[nb_users + nb_items, 2 * embedding_size])
bias = tf.get_variable('bias', shape=[nb_users + nb_items, 2])
priors = tf.constant(np_priors[:, None].repeat(embedding_size, axis=1), dtype=np.float32)

def make_mu():
    return tfd.Normal(loc=0., scale=1.)

def make_lambda():
    return tfd.Beta(1., 1.)

def make_embedding_prior():
    # return tfd.Normal(loc=[0.] * embedding_size, scale=[1.] * embedding_size)
    return wtfd.MultivariateNormalDiag(loc=[0.] * embedding_size, scale_diag=[1.] * embedding_size)

def make_embedding_prior2(mu0, lambda0):
    return wtfd.MultivariateNormalDiag(loc=[mu0] * embedding_size, scale_diag=[1/lambda0] * embedding_size)

def make_embedding_prior3(entity_batch):
    prior_prec_entity = tf.nn.embedding_lookup(priors, entity_batch)
    return wtfd.MultivariateNormalDiag(loc=[0.] * embedding_size, scale_diag=1000/prior_prec_entity)

def make_bias_prior():
    # return tfd.Normal(loc=0., scale=1.)
    return wtfd.Normal(loc=0., scale=1.)

def make_bias_prior2(mu0, lambda0):
    # return tfd.Normal(loc=0., scale=1.)
    return tfd.Normal(loc=mu0, scale=1/lambda0)

def make_bias_prior3(entity_batch):
    prior_prec_entity = tf.nn.embedding_lookup(priors, entity_batch)
    return tfd.Normal(loc=0., scale=1000/prior_prec_entity[:, 0])

def make_user_posterior(user_batch):
    feat_users = tf.nn.embedding_lookup(users, user_batch)
    # print('feat', feat_users)
    # return tfd.Normal(loc=feat_users[:, :embedding_size], scale=feat_users[:, embedding_size:])
    return wtfd.MultivariateNormalDiag(loc=feat_users[:, embedding_size:], scale_diag=tf.nn.softplus(feat_users[:, :embedding_size]))

def make_entity_bias(entity_batch):
    bias_batch = tf.nn.embedding_lookup(bias, entity_batch)
    # return tfd.Normal(loc=bias_batch[:, 0], scale=bias_batch[:, 1])
    return wtfd.Normal(loc=bias_batch[:, 0], scale=tf.nn.softplus(bias_batch[:, 1]))

# def make_item_posterior(item_batch):
#     items = tf.get_variable('items', shape=[nb_items, 2 * embedding_size])
#     feat_items = tf.nn.embedding_lookup(items, item_batch)
#     return tfd.Normal(loc=feat_items[:embedding_size], scale=feat_items[embedding_size:])

user_batch = tf.placeholder(tf.int32, shape=[None], name='user_batch')
item_batch = tf.placeholder(tf.int32, shape=[None], name='item_batch')
X_fm_batch = tf.sparse_placeholder(tf.int32, shape=[None, nb_users + nb_items], name='sparse_batch')
outcomes = tf.placeholder(tf.float32, shape=[None], name='outcomes')

mu0 = make_mu().sample()
lambda0 = make_lambda().sample()

all_entities = tf.constant(np.arange(nb_users + nb_items))

# emb_user_prior = make_embedding_prior2(mu0, lambda0)
# emb_item_prior = make_embedding_prior2(mu0, lambda0)
# emb_user_prior = make_embedding_prior3(all_entities)
# emb_item_prior = make_embedding_prior3(all_entities)
emb_user_prior = make_embedding_prior()
emb_item_prior = make_embedding_prior()
# bias_user_prior = make_bias_prior2(mu0, lambda0)
# bias_item_prior = make_bias_prior2(mu0, lambda0)
# bias_user_prior = make_bias_prior3(all_entities)
# bias_item_prior = make_bias_prior3(all_entities)
bias_user_prior = make_bias_prior()
bias_item_prior = make_bias_prior()

q_user = make_user_posterior(user_batch)
q_item = make_user_posterior(item_batch)
q_user_bias = make_entity_bias(user_batch)
q_item_bias = make_entity_bias(item_batch)

q_entity = make_user_posterior(all_entities)
q_entity_bias = make_entity_bias(all_entities)
all_bias = q_entity_bias.sample()
all_feat = q_entity.sample()
feat_users2 = tf.nn.embedding_lookup(all_feat, user_batch)
feat_items2 = tf.nn.embedding_lookup(all_feat, item_batch)
bias_users2 = tf.nn.embedding_lookup(all_bias, user_batch)
bias_items2 = tf.nn.embedding_lookup(all_bias, item_batch)

feat_users = q_user.sample()
print('sample feat users', feat_users)
feat_items = q_item.sample()
bias_users = q_user_bias.sample()
bias_items = q_item_bias.sample()
user_rescale = tf.nn.embedding_lookup(priors, user_batch)[:, 0]
item_rescale = tf.nn.embedding_lookup(priors, item_batch)[:, 0]
# print(prior.cdf(1.7))
# for _ in range(3):
#     print(prior.sample([2]))

# Predictions
def make_likelihood(feat_users, feat_items, bias_users, bias_items):
    logits = tf.reduce_sum(feat_users * feat_items, 1) + bias_users + bias_items
    return tfd.Bernoulli(logits)

def make_likelihood_reg(feat_users, feat_items, bias_users, bias_items):
    logits = tf.reduce_sum(feat_users * feat_items, 1) + bias_users + bias_items
    return tfd.Normal(logits, scale=sigma)

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
    return tfd.Normal(logits, scale=sigma)

# likelihood = make_likelihood(feat_users, feat_items, bias_users, bias_items)
likelihood = make_likelihood_reg(feat_users, feat_items, bias_users, bias_items)
sparse_pred = make_sparse_pred_reg(X_fm_batch)
pred2 = sparse_pred.mean()
ll = make_likelihood(feat_users2, feat_items2, bias_users2, bias_items2)
pred = likelihood.mean()
# print(likelihood.log_prob([1, 0]))

print('likelihood', likelihood.log_prob(outcomes))
print('prior', emb_user_prior.log_prob(feat_users))
print('scaled prior', emb_user_prior.log_prob(feat_users) / user_rescale)

print('posterior', q_user.log_prob(feat_users))
print('bias prior', bias_user_prior.log_prob(bias_users))
print('bias posterior', q_user_bias.log_prob(bias_users))

# sentinel = likelihood.log_prob(outcomes)
# sentinel = bias_prior.log_prob(bias_users)
sentinel = tf.reduce_sum(ll.log_prob(outcomes))
sentinel2 = tf.reduce_sum(likelihood.log_prob(outcomes))

# elbo = tf.reduce_mean(
#     user_rescale * item_rescale * likelihood.log_prob(outcomes) +
#     item_rescale * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
#                     emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
#     user_rescale * (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
#                     emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)))
elbo3 = tf.reduce_sum(
    likelihood.log_prob(outcomes) +
    1/user_rescale * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
                      emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
    1/item_rescale * (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
                      emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)))

elbo = tf.reduce_mean(
    # len(X_train) * likelihood.log_prob(outcomes) +
    len(X_train) * sparse_pred.log_prob(outcomes) +
    (nb_users + nb_items) / 2 * (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
                                 emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users) +
                                 bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
                                 emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)))

elbo4 = (# len(X_train) * tf.reduce_mean(ll.log_prob(outcomes)) +
        len(X_train) * sparse_pred.log_prob(outcomes) +
        tf.reduce_sum(bias_user_prior.log_prob(all_bias) - q_entity_bias.log_prob(all_bias)) +
        tf.reduce_sum(emb_user_prior.log_prob(all_feat) - q_entity.log_prob(all_feat)))

elbo2 = tf.reduce_mean(
    len(X_train) * likelihood.log_prob(outcomes) +
                     (bias_user_prior.log_prob(bias_users) - q_user_bias.log_prob(bias_users) +
                      emb_user_prior.log_prob(feat_users) - q_user.log_prob(feat_users)) +
                     (bias_item_prior.log_prob(bias_items) - q_item_bias.log_prob(bias_items) +
                      emb_user_prior.log_prob(feat_items) - q_item.log_prob(feat_items)))

optimizer = tf.train.AdamOptimizer(gamma)  # 0.001
# optimizer = tf.train.GradientDescentOptimizer(gamma)
infer_op = optimizer.minimize(-elbo)

indices_train = np.column_stack((np.arange(nb_train_samples).repeat(2), X_train.flatten()))
indices_test = np.column_stack((np.arange(nb_test_samples).repeat(2), X_test.flatten()))


with tf.Session() as sess:
    sess.run(tf.global_variables_initializer())

    for epoch in range(1, epochs + 1):
        # sess.run(iterator.initializer)
        # np.random.shuffle(X_train)
        # np.random.shuffle(X_fm_train)
        lbs = []
        # c = 0
        dt = time.time()
        for nb_iter in range(iters):
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
                                                          #X_fm_batch: X_fm_train[batch_ids]})
            lbs.append(lb)
            if nb_iter == 0:
                print(iters, 'times', time.time() - dt0)

            #except tf.errors.OutOfRangeError:
        # print('train', X_test[0])
        # print('train fm', X_fm_test[0])
        if VERBOSE:
            train_pred, train_pred2 = sess.run([pred, pred2], feed_dict={user_batch: X_train[:, 0],
                                                                     item_batch: X_train[:, 1],
                                                                     X_fm_batch: tf.SparseTensorValue(indices_train, np.ones(2 * nb_train_samples), [nb_train_samples, nb_users + nb_items])})
                                                                     # X_fm_batch: X_fm_train})
        
            print('Train ACC', np.mean(y_train == np.round(train_pred)))
            #print('Train AUC', roc_auc_score(y_train, train_pred))
            #print('Train NLL', log_loss(y_train, train_pred, eps=1e-6))
            print('Train RMSE', mean_squared_error(y_train, train_pred) ** 0.5)
            print('Train2 RMSE', mean_squared_error(y_train, train_pred2) ** 0.5)
            print('Pred', y_train[:5], train_pred[:5])
            print('Pred2', y_train[:5], train_pred2[:5])

        if VERBOSE or epoch == epochs or epoch % 10 == 0:
            test_pred, test_pred2 = sess.run([pred, pred2], feed_dict={user_batch: X_test[:, 0],
                                                                   item_batch: X_test[:, 1],
                                                                   X_fm_batch: tf.SparseTensorValue(indices_test, np.ones(2 * nb_test_samples), [nb_test_samples, nb_users + nb_items])})
                                                                   # X_fm_batch: X_fm_test})

            print('Test ACC', np.mean(y_test == np.round(test_pred)))
            # print('Test AUC', roc_auc_score(y_test, test_pred))
            # print('Test NLL', log_loss(y_test, test_pred, eps=1e-6))
            print('Test RMSE', mean_squared_error(y_test, test_pred) ** 0.5)
            print('Test2 RMSE', mean_squared_error(y_test, test_pred2) ** 0.5)
            print('Pred', y_test[:5], test_pred[:5])
            print('Pred2', y_test[:5], test_pred2[:5])

        print('{:.3f}s Epoch {}: Lower bound = {}'.format(
              time.time() - dt, epoch, np.mean(lbs) / nb_train_samples))
        # break
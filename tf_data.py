from scipy.sparse import coo_matrix, csr_matrix
import tensorflow as tf
import numpy as np
import time


# print('Start')
# dt = time.time()

# Handle data
# X_data = tf.data.Dataset.from_tensor_slices(X_fm_train)
# y_data = tf.data.Dataset.from_tensor_slices(y_train)
# user_data = tf.data.Dataset.from_tensor_slices(X_train[:, 0])
# item_data = tf.data.Dataset.from_tensor_slices(X_train[:, 1])
# dataset = tf.data.Dataset.zip((X_data, y_data, user_data, item_data)).batch(batch_size)
# iterator = dataset.make_initializable_iterator()
# X_fm_batch, outcomes, user_batch, item_batch = iterator.get_next()

# print('Stop', time.time() - dt)  # 16 seconds pour Movielens

# sess.run(iterator.initializer)

# x_batch, y_batch = sess.run(next_element)

M = np.array([[0, 1, 1], [1, 0, 0], [1, 0, 0.5], [0, 1, 1]])
print(M)

S = coo_matrix(M)
S_tf_val = tf.SparseTensor(indices=np.column_stack((S.row, S.col)), values=S.data, dense_shape=M.shape)

y_train = np.random.random(len(S.data))
batch_size = 2
nb_epochs = 2

X_data = tf.data.Dataset.from_tensor_slices(S_tf_val)
y_data = tf.data.Dataset.from_tensor_slices(y_train)
dataset = tf.data.Dataset.zip((X_data, y_data)).shuffle(10000).batch(batch_size).repeat(nb_epochs)
iterator = dataset.make_initializable_iterator()
X_fm_batch, outcomes = iterator.get_next()

with tf.Session() as sess:
    sess.run(iterator.initializer)
    while True:
        try:
            print(sess.run([X_fm_batch, outcomes]))
        except tf.errors.OutOfRangeError:
            print('Finished')
            break

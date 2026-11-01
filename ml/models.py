"""纯 numpy 实现的两个回归器：岭回归（闭式解）与单/双隐层 MLP（Adam）。

不依赖 scikit-learn，便于在任何装了 numpy 的环境里复现。
"""

from __future__ import annotations

import numpy as np


class Standardizer:
    def fit(self, X: np.ndarray):
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0)
        self.sd[self.sd < 1e-12] = 1.0
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sd


class RidgeRegression:
    """岭回归：w = (X^T X + αI)^-1 X^T y（在标准化后的特征上求解）"""

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.scaler = Standardizer()

    def fit(self, X: np.ndarray, y: np.ndarray):
        Xs = self.scaler.fit(X).transform(X)
        A = Xs.T @ Xs + self.alpha * np.eye(Xs.shape[1])
        b = Xs.T @ y
        self.w = np.linalg.solve(A, b)
        self.b = float(y.mean())
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.scaler.transform(X) @ self.w + self.b


class MLPRegressor:
    """两层隐层的全连接网络，ReLU 激活，Adam 优化，MSE 损失。"""

    def __init__(self, hidden=(64, 32), lr=1e-3, epochs=400, batch_size=64,
                 l2=1e-5, seed=42, patience=60):
        self.hidden = hidden
        self.lr = lr
        self.epochs = epochs
        self.batch_size = batch_size
        self.l2 = l2
        self.seed = seed
        self.patience = patience

    # ---------------- 内部 ----------------
    def _init_params(self, n_in: int, rng: np.random.Generator):
        sizes = [n_in, *self.hidden, 1]
        Ws, bs = [], []
        for i in range(len(sizes) - 1):
            # He 初始化
            W = rng.normal(0.0, np.sqrt(2.0 / sizes[i]), size=(sizes[i], sizes[i + 1]))
            Ws.append(W)
            bs.append(np.zeros(sizes[i + 1]))
        return Ws, bs

    @staticmethod
    def _forward(X, Ws, bs):
        acts = [X]
        h = X
        for i in range(len(Ws) - 1):
            h = np.maximum(h @ Ws[i] + bs[i], 0.0)
            acts.append(h)
        out = h @ Ws[-1] + bs[-1]
        return out.ravel(), acts

    # ---------------- 对外 ----------------
    def fit(self, X: np.ndarray, y: np.ndarray, X_val=None, y_val=None):
        rng = np.random.default_rng(self.seed)
        self.x_scaler = Standardizer().fit(X)
        Xs = self.x_scaler.transform(X)
        self.y_mu, self.y_sd = float(y.mean()), float(y.std() or 1.0)
        ys = (y - self.y_mu) / self.y_sd

        self.Ws, self.bs = self._init_params(Xs.shape[1], rng)
        m = [np.zeros_like(w) for w in self.Ws] + [np.zeros_like(b) for b in self.bs]
        v = [np.zeros_like(w) for w in self.Ws] + [np.zeros_like(b) for b in self.bs]
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        step = 0

        n = len(Xs)
        best = (np.inf, None, None)
        bad = 0
        self.history = []

        for epoch in range(self.epochs):
            order = rng.permutation(n)
            for start in range(0, n, self.batch_size):
                idx = order[start:start + self.batch_size]
                xb, yb = Xs[idx], ys[idx]
                pred, acts = self._forward(xb, self.Ws, self.bs)

                diff = (pred - yb).reshape(-1, 1)
                gW = [None] * len(self.Ws)
                gb = [None] * len(self.bs)
                delta = diff
                for l in range(len(self.Ws) - 1, -1, -1):
                    gW[l] = acts[l].T @ delta / len(idx) + self.l2 * self.Ws[l]
                    gb[l] = delta.mean(axis=0)
                    if l > 0:
                        delta = (delta @ self.Ws[l].T) * (acts[l] > 0)

                step += 1
                params = self.Ws + self.bs
                grads = gW + gb
                for i, (p_, g_) in enumerate(zip(params, grads)):
                    m[i] = beta1 * m[i] + (1 - beta1) * g_
                    v[i] = beta2 * v[i] + (1 - beta2) * (g_ ** 2)
                    mhat = m[i] / (1 - beta1 ** step)
                    vhat = v[i] / (1 - beta2 ** step)
                    p_ -= self.lr * mhat / (np.sqrt(vhat) + eps)

            # 每个 epoch 记录一次损失
            pred_tr, _ = self._forward(Xs, self.Ws, self.bs)
            train_mse = float(np.mean((pred_tr - ys) ** 2))
            if X_val is not None:
                pred_va = self.predict(X_val)
                val_mse = float(np.mean((pred_va - y_val) ** 2))
            else:
                val_mse = train_mse
            self.history.append((train_mse, val_mse))

            if val_mse < best[0] - 1e-9:
                best = (val_mse, [w.copy() for w in self.Ws], [b.copy() for b in self.bs])
                bad = 0
            else:
                bad += 1
                if bad >= self.patience:
                    break

        if best[1] is not None:
            self.Ws, self.bs = best[1], best[2]
        self.epochs_run = len(self.history)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        out, _ = self._forward(self.x_scaler.transform(X), self.Ws, self.bs)
        return out * self.y_sd + self.y_mu

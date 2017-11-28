"""The Discriminators."""
import functools
import itertools

import torch
from torch import nn
from torch.nn import functional as nnf
from torch.autograd import Variable

import environ


TYPES = ('cnn', 'rnn')
CNN, RNN = TYPES


def _l2_reg(mod, l=1e-4):
    def _reg(var, grad):
        return grad + l*var
    mod.weight.register_hook(functools.partial(_reg, mod.weight))
    mod.bias.register_hook(functools.partial(_reg, mod.bias))
    return mod


class Highway(nn.Module):
    """A Highway layer Module."""

    def __init__(self, in_features, activation=nn.ReLU(True)):
        super(Highway, self).__init__()

        self.gate = nn.Sequential(
            nn.Linear(in_features, in_features),
            nn.Sigmoid(),
        )

        self.tx = nn.Sequential(
            nn.Linear(in_features, in_features),
            activation,
        )

    def forward(self, x):
        g = self.gate(x)
        return g * self.tx(x) + (1 - g) * x


class Discriminator(nn.Module):
    """A base class for Discriminators."""

    def __init__(self, vocab_size, tok_emb_dim, **kwargs):
        super(Discriminator, self).__init__()

        padding_idx = None if kwargs.get('env') == environ.SYNTH else 0
        self.tok_emb = nn.Embedding(vocab_size, tok_emb_dim,
                                     padding_idx=padding_idx)

        self.stdev = nn.Parameter(torch.randn(1) * 0.1)

    def parameters(self, dx2=False):
        """
        Returns an iterator over module parameters.
        If dx2=True, only yield parameters that are twice differentiable.
        """
        if not dx2:
            return super(Discriminator, self).parameters()
        return itertools.chain(*[
            m.parameters() for m in self.children() if m != self.tok_emb])

    def forward(self, toks):
        """
        toks: N*T or [N*1]*T
        """
        if isinstance(toks, (list, tuple)):
            toks = torch.cat(toks, -1)
        logits = self._forward(toks)
        fuzz = Variable(logits.data.new(logits.size()).normal_()) * self.stdev
        return nnf.log_softmax(logits + fuzz, dim=1)



class CNNDiscriminator(Discriminator):
    """A CNN token discriminator."""

    def __init__(self, tok_emb_dim, filter_widths, num_filters, dropout,
                 **kwargs):
        super(CNNDiscriminator, self).__init__(
            tok_emb_dim=tok_emb_dim, **kwargs)

        assert len(filter_widths) == len(num_filters)

        cnn_layers = []
        for kw, c in zip(filter_widths, num_filters):
            cnn_layers.append(nn.Sequential(
                nn.Conv1d(tok_emb_dim, c, kw),
                nn.ReLU(True),
            ))
        self.cnn_layers = nn.ModuleList(cnn_layers)

        emb_dim = sum(num_filters)
        self.logits = nn.Sequential(
            Highway(emb_dim),
            nn.Dropout(dropout),
            _l2_reg(nn.Linear(emb_dim, 2)))

    def _forward(self, toks):
        """ toks: N*T """
        embs = self.tok_emb(toks).transpose(1, 2)  # N*d_wemb*T

        layer_acts = []  # num_layers*[N*c]
        for layer in self.cnn_layers:
            # layer_acts.append(layer(embs).max(-1)[0])
            layer_acts.append(layer(embs).mean(-1))
        layers_acts = torch.cat(layer_acts, -1)  # N*sum(num_filters)

        return self.logits(layers_acts)


class RNNDiscriminator(Discriminator):
    """An RNN token discriminator."""

    def __init__(self, tok_emb_dim, rnn_dim, **kwargs):
        super(RNNDiscriminator, self).__init__(tok_emb_dim=tok_emb_dim,
                                               **kwargs)

        self.rnn = nn.LSTM(tok_emb_dim, rnn_dim, num_layers=2,
                           bidirectional=True)

        self.logits = nn.Linear(rnn_dim * 2, 2)

    def _forward(self, toks):
        """
        toks: N*T
        """
        tok_embs = self.tok_emb(toks).transpose(0, 1)  # T*N*d_wemb
        seq_embs, _ = self.rnn(tok_embs)
        return self.logits(seq_embs[-1])


def create(d_type, d_tok_emb_dim, **opts):
    """Creates a token discriminator."""
    d_cls = RNNDiscriminator if d_type == RNN else CNNDiscriminator
    return d_cls(tok_emb_dim=d_tok_emb_dim, **opts)


def test_cnn_discriminator():
    """Tests the CNNDiscriminator."""
    # pylint: disable=unused-variable
    batch_size = 3
    vocab_size = 32
    tok_emb_dim = 10
    filter_widths = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20]
    num_filters = [100, 200, 200, 200, 200, 100, 100, 100, 100, 100, 160, 160]
    dropout = 0.25
    debug = True

    d = CNNDiscriminator(**locals())

    preds = d(Variable(torch.LongTensor(batch_size, 20).fill_(1)))
    assert preds.size(0) == batch_size
    assert preds.size(1) == 2
    assert torch.np.allclose(preds.data.exp().sum(1).numpy(), 1)

    preds.sum().backward(create_graph=True)
    sum(p.grad.norm() for p in d.parameters(dx2=True)).backward()


def test_rnn_discriminator():
    """Tests the RNNDiscriminator."""
    # pylint: disable=unused-variable
    batch_size = 3
    vocab_size = 32
    tok_emb_dim = 10
    rnn_dim = 4
    debug = True

    d = RNNDiscriminator(**locals())

    preds = d(Variable(torch.LongTensor(batch_size, 20).fill_(1)))
    assert preds.size(0) == batch_size
    assert preds.size(1) == 2
    assert torch.np.allclose(preds.data.exp().sum(1).numpy(), 1)

    preds.sum().backward(create_graph=True)
    sum(p.grad.norm() for p in d.parameters(dx2=True)).backward()

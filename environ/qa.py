"""An Environment for use with the QA dataset."""

import os
import logging

import torch
import torch.nn.functional as nnf
from torch.autograd import Variable

import common
import dataset
from .environment import Environment

class QAEnvironment(Environment):
    """Functions for training a model on the QA dataset."""

    @classmethod
    def get_opt_parser(cls):
        """Returns an `ArgumentParser` that parses env-specific opts."""
        parser = super(QAEnvironment, cls).get_opt_parser()
        parser.add_argument(
            '--data-dir', default='data/qa', type=os.path.abspath)
        parser.set_defaults(
            seqlen=22,
            vocab_size=20000,
            g_word_emb_dim=64,
            d_word_emb_dim=64,
            gen_dim=512,
            num_gen_layers=2,
            lr_g=0.001,
            lr_d=0.001,
            )
        return parser

    def __init__(self, opts):
        """Creates a QAEnvironment."""
        super(QAEnvironment, self).__init__(opts)

        self.train_dataset = dataset.QADataset(part='train', **vars(opts))
        self.val_dataset = dataset.QADataset(part='val', **vars(opts))

        self.init_toks.data.fill_(self.train_dataset.vocab[common.BOS])

    def pretrain_g(self):
        """Pretrains G using maximum-likelihood on the QA dataset."""

        logger = logging.getLogger()

        train_loader = self._create_dataloader(self.train_dataset)
        val_loader = self._create_dataloader(self.val_dataset)

        def _forward_batch(batch, volatile=False):
            toks, _ = batch
            toks = Variable(
                toks.view(-1, toks.size(-1)), volatile=volatile).cuda()
            flat_tgts = toks[:, 1:].t().contiguous().view(-1)

            gen_probs, _ = self.g(toks[:, :-1])
            flat_gen_probs = gen_probs.view(-1, gen_probs.size(-1))
            return nnf.nll_loss(flat_gen_probs, flat_tgts, ignore_index=0)

        for epoch in range(1, self.opts.pretrain_g_epochs + 1):
            train_loss = 0
            for batch in train_loader:
                loss = _forward_batch(batch)
                train_loss += loss.data[0]

                self.optim_g.zero_grad()
                loss.backward()
                self.optim_g.step()
            train_loss /= len(train_loader)

            val_loss = 0
            for batch in val_loader:
                val_loss += _forward_batch(batch, volatile=True).data[0]
            val_loss /= len(val_loader)

            logger.info(
                f'[{epoch}] loss: train={train_loss:.3f} val={val_loss:.3f}')

            gen_toks, _ = self.g.rollout(self.init_toks[:1], self.opts.seqlen)
            gen_toks = torch.cat(gen_toks, -1)
            logger.debug(self.train_dataset.decode(gen_toks.data[0]))

    def pretrain_d(self):
        pass

    def train_adv(self):
        pass

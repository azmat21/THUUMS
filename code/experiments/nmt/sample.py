#!/usr/bin/env python

import argparse
import cPickle
import traceback
import logging
import time
import sys

import numpy

import experiments.nmt
from experiments.nmt import\
    RNNEncoderDecoder,\
    prototype_state,\
    parse_input

from experiments.nmt.numpy_compat import argpartition

logger = logging.getLogger(__name__)

class Timer(object):

    def __init__(self):
        self.total = 0

    def start(self):
        self.start_time = time.time()

    def finish(self):
        self.total += time.time() - self.start_time

class BeamSearch(object):

    def __init__(self, enc_dec):
        self.enc_dec = enc_dec
        state = self.enc_dec.state
        self.eos_id = state['null_sym_target']
        self.unk_id = state['unk_sym_target']

    def compile(self):
        self.output_fn = self.enc_dec.create_predictor()

    def search(self, seq, n_samples, ignore_unk=False, minlen=1):
        output = self.output_fn(seq)[0]
        fin_trans = numpy.argmax(output,axis=1)
        fin_costs =  numpy.sum(output[numpy.arange(output.shape[0]), fin_trans])
	print fin_trans.shape
	print fin_trans
        return [fin_trans], [fin_costs], None

def indices_to_words(i2w, seq):
    sen = []
    for k in xrange(len(seq)):
        if i2w[seq[k]] == '<eol>':
            break
        sen.append(i2w[seq[k]])
    return sen

def sample(lm_model, seq, n_samples,
        sampler=None, beam_search=None,
        ignore_unk=False, normalize=False,
        alpha=1, verbose=False):
    if beam_search:
        sentences = []
        trans, costs, aligns = beam_search.search(seq, n_samples,
                ignore_unk=ignore_unk, minlen=len(seq) / 2)
        if normalize:
            counts = [len(s) for s in trans]
            costs = [co / cn for co, cn in zip(costs, counts)]
        for i in range(len(trans)):
            sen = indices_to_words(lm_model.word_indxs, trans[i])
            sentences.append(" ".join(sen))
        for i in range(len(costs)):
            if verbose:
                print "{}: {}".format(costs[i], sentences[i])
        return sentences, costs, trans, aligns
    elif sampler:
        sentences = []
        all_probs = []
        costs = []

        values, cond_probs = sampler(n_samples, 3 * (len(seq) - 1), alpha, seq)
        for sidx in xrange(n_samples):
            sen = []
            for k in xrange(values.shape[0]):
                if lm_model.word_indxs[values[k, sidx]] == '<eol>':
                    break
                sen.append(lm_model.word_indxs[values[k, sidx]])
            sentences.append(" ".join(sen))
            #probs = cond_probs[:, sidx]
            probs = numpy.array(cond_probs[:len(sen) + 1, sidx])
            all_probs.append(numpy.exp(-probs))
            costs.append(-numpy.sum(probs))
        if normalize:
            counts = [len(s.strip().split(" ")) for s in sentences]
            costs = [co / cn for co, cn in zip(costs, counts)]
        sprobs = numpy.argsort(costs)
        if verbose:
            for pidx in sprobs:
                print "{}: {} {} {}".format(pidx, -costs[pidx], all_probs[pidx], sentences[pidx])
            print
        return sentences, costs, None, None
    else:
        raise Exception("I don't know what to do")


def parse_args():
    parser = argparse.ArgumentParser(
            "Sample (of find with beam-serch) translations from a translation model")
    parser.add_argument("--state",
            required=True, help="State to use")
    parser.add_argument("--beam-search",
            action="store_true", help="Beam size, turns on beam-search")
    parser.add_argument("--beam-size",
            type=int, help="Beam size")
    parser.add_argument("--alignment",
            action="store_true", help="turns on to output the alignment info")
    parser.add_argument("--nbest",
            action="store_true", help="output nbest results (with scores), turns on nbest")
    parser.add_argument("--ignore-unk",
            default=False, action="store_true",
            help="Ignore unknown words")
    parser.add_argument("--source",
            help="File of source sentences")
    parser.add_argument("--trans",
            help="File to save translations in")
    parser.add_argument("--normalize",
            action="store_true", default=False,
            help="Normalize log-prob with the word count")
    parser.add_argument("--verbose",
            action="store_true", default=False,
            help="Be verbose")
    parser.add_argument("model_path",
            help="Path to the model")
    parser.add_argument("changes",
            nargs="?", default="",
            help="Changes to state")
    return parser.parse_args()

def main():
    args = parse_args()

    state = prototype_state()
    with open(args.state) as src:
        state.update(cPickle.load(src))
    state.update(eval("dict({})".format(args.changes)))

    logging.basicConfig(level=getattr(logging, state['level']), format="%(asctime)s: %(name)s: %(levelname)s: %(message)s")

    rng = numpy.random.RandomState(state['seed'])

    ###########################################################
    # by He Wei
    #enc_dec = RNNEncoderDecoder(state, rng, skip_init=True)
    enc_dec = RNNEncoderDecoder(state, rng, skip_init=True, compute_alignment=True)
    ###########################################################

    enc_dec.build()
    lm_model = enc_dec.create_lm_model()
    lm_model.load(args.model_path)
    indx_word = cPickle.load(open(state['word_indx'],'rb'))

    sampler = None
    beam_search = None
    if args.beam_search:
        beam_search = BeamSearch(enc_dec)
        beam_search.compile()
    else:
        sampler = enc_dec.create_sampler(many_samples=True)

    idict_src = cPickle.load(open(state['indx_word'],'r'))

    if args.source and args.trans:
        # Actually only beam search is currently supported here
        #assert beam_search
        #assert args.beam_size

        fsrc = open(args.source, 'r')
        ftrans = open(args.trans, 'w')

        start_time = time.time()

        #n_samples = args.beam_size
        total_cost = 0.0
        #logging.debug("Beam size: {}".format(n_samples))
        for i, line in enumerate(fsrc):
            seqin = line.strip()
            seq, parsed_in = parse_input(state, indx_word, seqin, idx2word=idict_src)
            if args.verbose:
                print "Parsed Input:", parsed_in

            if args.beam_search:
                trans, costs, _, aligns = sample(lm_model, seq, args.beam_size, sampler=sampler,
                        beam_search=beam_search, ignore_unk=args.ignore_unk, normalize=args.normalize)
            else:
                trans, costs, _, aligns = sample(lm_model, seq, 1, sampler=sampler,
                        beam_search=beam_search, ignore_unk=args.ignore_unk, normalize=args.normalize)
            best = numpy.argmin(costs)
            out_str = trans[best]
            align_str = []

            if args.beam_search and args.alignment:
                for (idx, _a) in enumerate(aligns[best]):
                    align_str.append("[%s]" % ' '.join(map(str, _a)))
                    #align_str.append("[%d-%d:%f,%d-%d:%f]" % (idx, _a[0], _a[1], idx, _a[2], _a[3]))
                out_str += "\t" + ' '.join(align_str)

            if args.beam_search and args.nbest:
                nbest_trans = trans
                nbest_costs = costs
                nbest_trans = numpy.array(nbest_trans)[numpy.argsort(nbest_costs)]
                nbest_costs = numpy.array(sorted(nbest_costs))
                nbest_str = ' ||| '.join("%s | %f" % (t, c) for (t, c) in zip(nbest_trans, nbest_costs))
                out_str += "\t" + nbest_str

            print >>ftrans, out_str

            if args.verbose:
                print "[Translation]%s\t[Align]%s" % (trans[best], ' '.join(align_str))
            total_cost += costs[best]
            if (i + 1)  % 100 == 0:
                ftrans.flush()
                logger.debug("Current speed is {} per sentence".
                        format((time.time() - start_time) / (i + 1)))
        print "Total cost of the translations: {}".format(total_cost)
        print "Total used time: {}".format(time.time() - start_time)

        fsrc.close()
        ftrans.close()
    else:
        while True:
            try:
                seqin = raw_input('Input Sequence: ')
                n_samples = int(raw_input('How many samples? '))
                alpha = None
                if not args.beam_search:
                    alpha = float(raw_input('Inverse Temperature? '))
                seq,parsed_in = parse_input(state, indx_word, seqin, idx2word=idict_src)
                print "Parsed Input:", parsed_in
            except Exception:
                print "Exception while parsing your input:"
                traceback.print_exc()
                continue

            sample(lm_model, seq, n_samples, sampler=sampler,
                    beam_search=beam_search,
                    ignore_unk=args.ignore_unk, normalize=args.normalize,
                    alpha=alpha, verbose=True)

if __name__ == "__main__":
    main()

#!/usr/bin/env python
# microsynteny.py
# v1.0 2015-10-09

'''microsynteny.py v1.3 last modified 2019-09-27

microsynteny.py -q query.gtf -d ref_species.gtf -b query_vs_ref_blast.tab -E ../bad_contigs -g -D '_' --blast-query-delimiter '.' > query_vs_ref_microsynteny.tab

    FOR GENERATION OF BLAST DATA TO LINK SETS
blastx -query query.fasta -db ref_prots.fasta -outfmt 6 -evalue 1e-5 > query_vs_ref_blast.tab

    large blast.tab files can be gzipped as .gz

    IN CASE OF NO OUTPUT, CHECK THAT -Q AND -D ARE SET CORRECTLY
    from gtf files, gene_id is extracted
    the blast query names must match the gene_id
    for example, if gene_id is avic.12345 and blast query is avic.12345.1
    set --blast-query-delimiter '.'

    TWO OUTPUT OPTIONS:
    tab delimited text file of 12 columns, consisting of:
query-scaffold  ref-scaffold  block-number
  query-gene  start  end  strand  ref-gene  start  end  strand  score

    OR GFF format file (use --make-gff flag)
    for Parent feature:
    score is length of the block, target is scaffold of subject
    for match_part feature:
    score is bitscore of the blast hit

    THIS CANNOT DETECT ERRONEOUS FUSION OR SPLITTING OF GENES
    i.e. three collinear genes in the query that are erroneously fused
    in the ref species will still count as a block of three

    to determine the correct minimum -m, check the same dataset
    as randomized queries, using -R
    blocks of 2 occur often, but 3 is rare, so -m 3 is usually sufficient
'''

import sys
import re
import os
import time
import argparse
import random
import gzip
from collections import namedtuple,defaultdict

querygene = namedtuple("querygene", "start end strand")
refgene = namedtuple("refgene", "scaffold start end strand")

def parse_gtf(gtffile, exonstogenes, excludedict, delimiter, isref=False):
	'''from a gtf, return a dict of dicts where keys are scaffold names, then gene names, and values are gene info as a tuple of start end and strand direction'''
	if gtffile.rsplit('.',1)[-1]=="gz": # autodetect gzip format
		opentype = gzip.open
		sys.stderr.write("# Parsing {} as gzipped  ".format(gtffile) + time.asctime() + os.linesep)
	else: # otherwise assume normal open for fasta format
		opentype = open
		sys.stderr.write("# Parsing {}  ".format(gtffile) + time.asctime() + os.linesep)

	if isref: # meaning is db/subject, thus get normal dictionary
		genesbyscaffold = {}
	else:
		genesbyscaffold = defaultdict(dict) # scaffolds as key, then gene name, then genemapping tuple
	if exonstogenes:
		nametoscaffold = {} # in order to get transcript boundaries, store names to scaffolds
		nametostrand = {} # store strand by gene ID
		exonboundaries = defaultdict(list) # make list of tuples of exons by transcript, to determine genes
	for line in opentype(gtffile,'rt'):
		line = line.strip()
		if line and not line[0]=="#": # ignore empty lines and comments
			lsplits = line.split("\t")
			scaffold = lsplits[0]
			if excludedict and excludedict.get(scaffold, False):
				continue # skip anything that hits to excludable scaffolds
			feature = lsplits[2]
			attributes = lsplits[8]
			if feature=="gene" or feature=="transcript" or feature=="mRNA":
				try:
					geneid = re.search('gene_id "([\w.|-]+)"', attributes).group(1)
				except AttributeError: # in case re fails and group does not exist
					geneid = re.search('ID=([\w.|-]+)', attributes).group(1)
				# if a delimiter is given for either query or db, then split
				if delimiter:
					geneid = geneid.rsplit(delimiter,1)[0]

				# generate tuple differently for query and db
				if isref:
					refbounds = refgene(scaffold=lsplits[0], start=int(lsplits[3]), end=int(lsplits[4]), strand=lsplits[6] )
					genesbyscaffold[geneid] = refbounds
				else:
					boundstrand = querygene(start=int(lsplits[3]), end=int(lsplits[4]), strand=lsplits[6] )
					genesbyscaffold[scaffold][geneid] = boundstrand
			# if using exons only, then start collecting exons
			elif (exonstogenes and feature=="exon"):
				try:
					geneid = re.search('gene_id "([\w.|-]+)"', attributes).group(1)
				except AttributeError: # in case re fails and group does not exist
					geneid = re.search('name "([\w.|-]+)"', attributes).group(1)
				nametoscaffold[geneid] = scaffold
				nametostrand[geneid] = lsplits[6]
				exonbounds = (int(lsplits[3]), int(lsplits[4]))
				exonboundaries[geneid].append(exonbounds) # for calculating gene boundaries

	if len(genesbyscaffold) > 0: # even if no-genes was set, this should be more than 0 if genes were in one gtf
		if isref:
			sys.stderr.write("# Found {} genes  ".format( len(genesbyscaffold) ) + time.asctime() + os.linesep)
		else:
			sys.stderr.write("# Found {} genes  ".format(sum( list(map( len,genesbyscaffold.values())) ) ) + time.asctime() + os.linesep)
		return genesbyscaffold
	else: # generate gene boundaries by scaffold
		sys.stderr.write("# Estimated {} genes from {} exons  ".format(len(exonboundaries), sum(len(x) for x in exonboundaries.values() ) ) + time.asctime() + os.linesep)
		for gene,exons in exonboundaries.items():
			if isref: # make different tuple for reference genes, since they are indexed by name, not scaffold
				refbounds = refgene(scaffold=nametoscaffold[gene], start=min(x[0] for x in exons), end=max(x[1] for x in exons), strand=nametostrand[gene] )
				genesbyscaffold[gene] = refbounds
			else:
				boundstrand = querygene(start=min(x[0] for x in exons), end=max(x[1] for x in exons), strand=nametostrand[gene] )
				genesbyscaffold[nametoscaffold[gene]][gene] = boundstrand
		sys.stderr.write("# Found {} genes  ".format(len(genesbyscaffold) ) + time.asctime() + os.linesep) # uses len here
		return genesbyscaffold

def parse_tabular_blast(blasttabfile, evaluecutoff, querydelimiter, refdelimiter, switchquery=False, maxhits=100):
	'''read tabular blast file, return a dict where key is query ID and value is subject ID'''
	if blasttabfile.rsplit('.',1)[-1]=="gz": # autodetect gzip format
		opentype = gzip.open
		sys.stderr.write("# Parsing tabular blast output {} as gzipped  ".format(blasttabfile) + time.asctime() + os.linesep)
	else: # otherwise assume normal open for fasta format
		opentype = open
		sys.stderr.write("# Parsing tabular blast output {}  ".format(blasttabfile) + time.asctime() + os.linesep)
	query_to_sub_dict = defaultdict(dict)
	query_hits = defaultdict(int) # counter of hits
	evalueRemovals = 0
	for line in opentype(blasttabfile, 'rt'):
		line = line.strip()
		lsplits = line.split("\t")
		# qseqid, sseqid, pident, length, mismatch, gapopen, qstart, qend, sstart, send, evalue, bitscore
		if switchquery:
			queryseq = lsplits[1].rsplit(refdelimiter,1)[0]
			subjectid = lsplits[0].rsplit(querydelimiter,1)[0]
		else:
			queryseq = lsplits[0].rsplit(querydelimiter,1)[0]
			subjectid = lsplits[1].rsplit(refdelimiter,1)[0]

		# filter by evalue
		if float(lsplits[10]) > evaluecutoff:
			evalueRemovals += 1
			continue
		# filter by number of hits
		if query_hits.get(queryseq,0) >= maxhits: # too many hits already, skip
			continue
		# otherwise add the entry
		bitscore = float(lsplits[11])
		query_to_sub_dict[queryseq][subjectid] = bitscore
		query_hits[queryseq] += 1
	sys.stderr.write("# Found blast hits for {} query sequences  ".format( len(query_to_sub_dict) ) + time.asctime() + os.linesep)
	sys.stderr.write("# Removed {} hits by evalue, kept {} hits\n".format( evalueRemovals, sum(query_hits.values()) ) )
	sys.stderr.write("# Names parsed as {} from {}, and {} from {}\n".format( queryseq,lsplits[0], subjectid,lsplits[1] ) )
	return query_to_sub_dict

def randomize_genes(refdict):
	'''take the gtf dict and randomize the gene names for all genes, return a similar dict of dicts'''
	genepositions = {} # store gene positions as tuples
	randomgenelist = []
	sys.stderr.write("# Randomizing query gene positions  " + time.asctime() + os.linesep)
	for scaffold, genedict in refdict.items(): # iterate first to get list of all genes
		for genename in genedict.keys():
			randomgenelist.append(genename)
			genepositions[genename] = genedict[genename]
	# randomize the list
	random.shuffle(randomgenelist)
	# reiterate in same order, but store random gene names at the same position
	genecounter = 0
	randomgenesbyscaf = defaultdict(dict) # scaffolds as key, then gene name, then genemapping tuple
	for scaffold, genedict in refdict.items(): # iterate again to reassign genes to each scaffold
		for genename, bounds in genedict.items():
			randomgenesbyscaf[scaffold][randomgenelist[genecounter]] = genepositions[genename]
			genecounter += 1
	sys.stderr.write("# Randomized {} genes  ".format(genecounter) + time.asctime() + os.linesep)
	return randomgenesbyscaf

def synteny_walk(querydict, blastdict, refdict, min_block, max_span, max_distance, is_verbose, wayout, make_gff):
	'''for each query scaffold, begin with the first gene and follow as far as possible to identify colinear blocks, then print to stdout'''
	syntenylist = [] # list to keep track of matches, as tuples of (querygene_name, subject_name)
	blocknum = 1
	blocklengths = defaultdict(int) # dictionary to keep track of number of blocks of length N
	scaffoldgenecounts = defaultdict(int)
	splitgenes = 0 # counter for number of genes where next gene hits same reference, so query might be split
	basetotal = 0 # counter for block length in bases

	lastmatch = "" # string of name of last gene that matched

	querypos = (0,1) # position of first gene on query scaffold
	subjectpos = (0,1) # position of first gene on ref scaffold

	sys.stderr.write("# searching for colinear blocks of at least {} genes, with up to {} intervening genes\n".format( min_block, max_span ) )
	for scaffold, transdict in sorted(querydict.items(), key=lambda x: x[0]):
		orderedtranslist = sorted(transdict.items(), key=lambda x: x[1].start) # sort by start position
		genesonscaff = len(orderedtranslist) # keeping track of number of genes by scaffold, for scale
		scaffoldgenecounts[genesonscaff] += 1
		if is_verbose:
			sys.stderr.write("#1 Scanning scaffold {0} with {1} genes\n".format(scaffold, genesonscaff) )
		if genesonscaff < min_block: # not enough genes, thus no synteny would be found
			if is_verbose:
				sys.stderr.write("#1 Only {1} genes on scaffold {0}, skipping scaffold\n".format(scaffold, genesonscaff) )
			continue
		accounted_query_genes = [] # list of genes already in synteny blocks on this contig
		for i, querygene_tuple in enumerate(orderedtranslist):
			walksteps = max_span # genes until drop, walk starts new for each transcript
			accounted_matches = [] # list of genes already in synteny blocks on this contig
			# starting from each transcript
			startingtrans = querygene_tuple[0]
			querypos = (querygene_tuple[1].start, querygene_tuple[1].end)
			# get the blast matches of the query
			blastrefmatch_dict = blastdict.get(startingtrans,None) 
			if blastrefmatch_dict==None: # if no blast match, then skip to next gene
				if is_verbose:
					sys.stderr.write("#2 No blast matches for {}, skipping walk\n".format(startingtrans) )
				continue
			# otherwise start iterating through all blast hits of that gene
			for blast_refgene, bitscore1 in sorted(blastrefmatch_dict.items(), key=lambda x: x[1], reverse=True):
				if blast_refgene==lastmatch: # unique test to see if blast hit matches previous
					splitgenes += 1
				# if gene is already in a synteny block, ignore it
				# due to multiple hits within the same protein, e.g. multidomain proteins
				if startingtrans in accounted_query_genes:
					if is_verbose:
						sys.stderr.write("#2 Gene {} already has match on {}, skipping\n".format(startingtrans, scaffold) )
					continue
				# renew synteny list for each query gene
				syntenylist = [ (startingtrans,blast_refgene) ]

				if i < genesonscaff - 1: # this allows for 2 genes left
					if is_verbose:
						sys.stderr.write("#3 Starting walk from gene {} on scaffold {} against {}\n".format(startingtrans, scaffold, blast_refgene) )
					# get scaffold and position of matched gene
					refscaffold = refdict[blast_refgene].scaffold
					subjectpos = (refdict[blast_refgene].start, refdict[blast_refgene].end)
					######################################
					# begin of gene walk on forward strand
					for next_gene, next_gene_info in orderedtranslist[i+1:]: # getting next transcript, and next gene
						if walksteps > 0:
							dist_to_next_query = next_gene_info.start-querypos[1]
							if dist_to_next_query > max_distance: # next gene is too far
								if is_verbose:
									sys.stderr.write("#4 Next gene {} bases away from {}, stopping walk\n".format(dist_to_next_query, next_gene) )
								break # end gene block
							# update query positions
							querypos = (next_gene_info.start, next_gene_info.end)
							next_match_dict = blastdict.get(next_gene,None)
							# if no blast match, then skip to next walk step, and decrement
							if next_match_dict==None:
								if is_verbose:
									sys.stderr.write("#4 No blast matches for {}, skipping gene\n".format(next_gene) )
								walksteps -= 1
								continue
							# otherwise iterate through matches, finding one within range
							for next_match, bitscoreN in sorted(next_match_dict.items(), key=lambda x: x[1], reverse=True):
								if next_match==blast_refgene: # if last match is the same as current match
									accounted_query_genes.append(next_gene)
									continue # since it is still the same gene, move on but do not penalize
								else:
									next_ref_scaf = refdict[next_match].scaffold
									next_ref_pos = (refdict[next_match].start, refdict[next_match].end)
								if next_ref_scaf==refscaffold: # scaffolds match
									# determine distance, strand is not considered
									# meaning one value should be negative, other should be positive
									dist_to_next_ref = next_ref_pos[0] - subjectpos[1]
									dist_to_prev_ref = subjectpos[0] - next_ref_pos[1]
									# if either are greater than max distance, then gene is too far
									if dist_to_next_ref > max_distance or dist_to_prev_ref > max_distance:
										if is_verbose:
											sys.stderr.write("#4 {} match to {} is too far, {}bp, ignoring match\n".format(next_gene, next_match, max([dist_to_next_ref,dist_to_prev_ref]) ) )
										continue
									if is_verbose:
										sys.stderr.write("#5 Match {} found for {} on {}\n".format(next_match, next_gene, scaffold ) )
									walksteps = max_span # if a gene is found, reset steps
									subjectpos = next_ref_pos
									accounted_query_genes.append(next_gene)
									syntenylist.append( (next_gene,next_match) )
									break
								else:
									if is_verbose:
										sys.stderr.write("#4 {} matches {} on wrong contig {}, skipping gene\n".format(next_gene, next_match, next_ref_scaf) )
							else:
								walksteps -= 1 # then skip to next walk step and decrement
						else: # if walksteps is 0, then break out of for loop
							if is_verbose:
								sys.stderr.write("#3 Limit reached at {}, stopping walk for {}\n".format(next_gene, startingtrans) )
							break # no more searching for genes after walksteps is 0
					### WRITE LONGEST MATCH
					blocklen = len(syntenylist)
					if blocklen >= min_block:
						if is_verbose:
							sys.stderr.write("# Found block of {0} genes starting from {1} on {2}\n".format(blocklen, startingtrans, scaffold) )
						try:
							if blocklen > max(blocklengths.keys()):
								sys.stderr.write("New longest block blk-{} of {} on {}\n".format(blocknum, blocklen, scaffold) )
						except ValueError: # for first check where blocklengths is empty
							sys.stderr.write("New longest block blk-{} of {} on {}\n".format(blocknum, blocklen, scaffold) )
						qblockstart = transdict[syntenylist[0][0]].start
						qblockend = transdict[syntenylist[-1][0]].end
						sblockstart = refdict[syntenylist[0][1]].start
						sblockend = refdict[syntenylist[-1][1]].end
						basetotal += qblockend - qblockstart
						blocklengths[blocklen] += 1
						if sblockend > sblockstart:
							strand = "+"
						else:
							strand = "-"
							sblockstart, sblockend = sblockend, sblockstart
						###############################
						# generate GFF for entire block
						if make_gff:
							# could also be "cross_genome_match"
							blockline = "{0}\tmicrosynteny\tmatch\t{1}\t{2}\t{3}\t{4}\t.\tID=blk-{5};Name=blk-{5}_to_{6};Target={6} {7} {8}\n".format( scaffold, qblockstart, qblockend, blocklen, strand, blocknum, refscaffold, sblockstart, sblockend)
							wayout.write(blockline)
							# make GFF line for each match
							for j,pair in enumerate(syntenylist):
								outline = "{0}\tmicrosynteny\tmatch_part\t{1}\t{2}\t{3}\t{4}\t.\tID=blk-{5}.{10}.{11};Parent=blk-{5};Target={6} {7} {8} {9}\n".format( scaffold, transdict[pair[0]].start, transdict[pair[0]].end, blastdict[pair[0]][pair[1]], transdict[pair[0]].strand, blocknum, pair[1], refdict[pair[1]].start, refdict[pair[1]].end, refdict[pair[1]].strand, j+1, pair[0])
								wayout.write(outline)
						############################
						# otherwise use output of v1
						else:
							for pair in syntenylist:
								outline = "{}\t{}\tblk-{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(scaffold, refscaffold, blocknum, pair[0], transdict[pair[0]].start, transdict[pair[0]].end, transdict[pair[0]].strand, pair[1], refdict[pair[1]].start, refdict[pair[1]].end, refdict[pair[1]].strand, blastdict[pair[0]][pair[1]])
								wayout.write(outline)
						blocknum += 1
					else:
						if is_verbose:
							sys.stderr.write("# Block only contained {0} genes, ignoring block\n".format(blocklen) )
				else:
					if is_verbose:
						sys.stderr.write("# Too few genes left from {} on scaffold {}, skipping walk\n".format(startingtrans, scaffold) )
				lastmatch = str(blast_refgene)
	sys.stderr.write("# Found {} possible split genes  ".format(splitgenes) + time.asctime() + os.linesep)
	sys.stderr.write("# Most genes on a query scaffold was {}  ".format(max(list(scaffoldgenecounts.keys()))) + time.asctime() + os.linesep)
	genetotal = sum(x*y for x,y in blocklengths.items())
	sys.stderr.write("# Found {} total putative synteny blocks for {} genes  ".format(sum(list(blocklengths.values())), genetotal) + time.asctime() + os.linesep)
	if len(blocklengths)==0: # if no blocks are found
		sys.exit("### NO SYNTENY DETECTED, CHECK GENE ID FORMAT PARAMTERS -Q -D")
	sys.stderr.write("# Average block is {:.2f}, longest block was {} genes  ".format( 1.0*genetotal/sum(list(blocklengths.values())), max(list(blocklengths.keys())) ) + time.asctime() + os.linesep)
	sys.stderr.write("# Total block span was {} bases  ".format(basetotal) + time.asctime() + os.linesep)

	### MAKE BLOCK HISTOGRAM
	for k,v in sorted(blocklengths.items(),key=lambda x: x[0]):
		sys.stderr.write("{} {}\n".format(k, v) )
	# no return

def main(argv, wayout):
	if not len(argv):
		argv.append("-h")
	parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
	parser.add_argument('-b','--blast', help="tabular blast output", required=True)
	parser.add_argument('-q','--query-gtf', help="gtf file of query genes", required=True)
	parser.add_argument('-d','--db-gtf', help="gtf file of reference genes", required=True)
	parser.add_argument('-Q','--query-delimiter', help="gene transcript separator for query [.]")
	parser.add_argument('-D','--db-delimiter', help="gene transcript separator for db [.]")
	parser.add_argument('--blast-query-delimiter', help="gene transcript separator for blast query [|]", default='|')
	parser.add_argument('--blast-db-delimiter', help="gene transcript separator for blast ref [|]", default='|')
	parser.add_argument('-e','--evalue', type=float, default=1e-4, help="evalue cutoff for post blast filtering [1e-4]")
	parser.add_argument('-E','--exclude', help="file of list of bad contigs, from either genome")
	parser.add_argument('-g','--no-genes', action="store_true", help="genes are not defined, get gene ID for each exon")
	parser.add_argument('-m','--minimum', type=int, default=3, help="minimum syntenic genes to keep block, must be >=2 [3]")
	parser.add_argument('-s','--span', type=int, default=5, help="max number of skippable genes [5]")
	parser.add_argument('-z','--distance', type=int, default=30000, help="max distance on query scaffold before next gene [30000]")
	parser.add_argument('-G','--make-gff', help="make GFF output, instead of tabular blocks", action="store_true")
	parser.add_argument('-R','--randomize', help="randomize positions of query GTF", action="store_true")
	parser.add_argument('-S','--switch-query', help="switch query and subject", action="store_true")
	parser.add_argument('-v','--verbose', help="verbose output", action="store_true")
	args = parser.parse_args(argv)

	sys.stderr.write("# Running command:\n{}\n".format( ' '.join(sys.argv) ) )

	if args.minimum < 2:
		sys.stderr.write("WARNING: MINIMUM COLINEARITY -m MUST BE GREATER THAN 1, {} GIVEN\n".format(args.minimum) )
		sys.stderr.write("SETTING MINIMUM COLINEARITY TO 2\n")
		args.minimum = 2

	if args.exclude:
		sys.stderr.write("# Reading exclusion list {}  ".format(args.exclude) + time.asctime() + os.linesep)
		exclusionDict = {}
		for term in open(args.exclude,'r').readlines():
			term = term.strip()
			if term[0] == ">":
				term = term[1:]
			exclusionDict[term] = True
		sys.stderr.write("# Found {} contigs to exclude  ".format(len(exclusionDict) ) + time.asctime() + os.linesep)
	else:
		exclusionDict = None

	### SETUP DICTIONARIES ###
	if args.switch_query:
		querydict = parse_gtf(args.db_gtf, args.no_genes, exclusionDict), args.db_delimiter
		refdict = parse_gtf(args.query_gtf, args.no_genes, exclusionDict, args.query_delimiter, isref=True)
		blastdict = parse_tabular_blast(args.blast, args.evalue, args.blast_query_delimiter, args.blast_db_delimiter, args.switch_query)
	else:
		querydict = parse_gtf(args.query_gtf, args.no_genes, exclusionDict, args.query_delimiter)
		refdict = parse_gtf(args.db_gtf, args.no_genes, exclusionDict, args.db_delimiter, isref=True)
		blastdict = parse_tabular_blast(args.blast, args.evalue, args.blast_query_delimiter, args.blast_db_delimiter)

	### IF DOING RANDOMIZATION ###
	if args.randomize:
		querydict = randomize_genes(querydict)

	if args.make_gff:
		sys.stderr.write("# make GFF output: {}\n".format( args.make_gff ) )
	### START SYNTENY WALKING ###
	synteny_walk(querydict, blastdict, refdict, args.minimum, args.span, args.distance,  args.verbose, wayout, args.make_gff)

if __name__ == "__main__":
	main(sys.argv[1:],sys.stdout)

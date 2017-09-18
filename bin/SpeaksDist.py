#! /usr/local/bin/python

import sqlite3

import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

speaks_hist = []
conn = sqlite3.connect('mittab/pairing_db.sqlite3')
cursor = conn.cursor()
row_count = 0

for row in cursor.execute('SELECT speaks, count(*) FROM tab_roundstats GROUP BY speaks ORDER BY speaks'):
    row_count += 1
    speak, count = row
    speak, count = int(speak), int(count)
    speaks_hist.extend([speak] * count)

sns.distplot(speaks_hist, bins=row_count)
plt.show()

def pearsonMedianSkew(dist):
    mean, med, s = np.mean(dist), np.median(dist), np.std(dist)
    return (3 * (mean - med)) / s

print('Speaks standard deviation: %.3f' % np.std(speaks_hist))
print('Speaks Pearson skewness: %.3f' % pearsonMedianSkew(speaks_hist))

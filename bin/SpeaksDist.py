#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 13 18:36:46 2017

@author: josepheddy
"""

import matplotlib.pyplot as plt
import seaborn as sns

import numpy as np

#W&M
data_raw = \
['15|1',
'17|1',
'19|2',
'20|9',
'21|1',
'22|4',
'23|10',
'24|3',
'25|24',
'26|4',
'27|10',
'28|13',
'29|7',
'30|45',
'31|10',
'32|11',
'33|22',
'34|10',
'35|27',
'36|6',
'37|4',
'38|4',
'40|4']

speaks_hist = []
for data_point in data_raw:
    speak, count = tuple(data_point.split('|'))
    speak, count = int(speak), int(count)
    speaks_hist.extend([speak] * count)  
    
sns.distplot(speaks_hist, bins=len(data_raw))
plt.show()

def pearsonMedianSkew(dist):
    mean, med, s = np.mean(dist), np.median(dist), np.std(dist)
    return (3 * (mean - med)) / s

print('Speaks standard deviation: %.3f' % np.std(speaks_hist))
print('Speaks Pearson skewness: %.3f' % pearsonMedianSkew(speaks_hist))
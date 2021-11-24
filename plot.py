import numpy as np
import matplotlib.pyplot as plt

xvals = np.loadtxt('xvals.txt',dtype=float)
yvals = np.loadtxt('yvals.txt',dtype=float)

plt.plot(xvals,yvals)
plt.axhline(y=0.2, color='r', linestyle='-')
plt.axhline(y=-0.2, color='r', linestyle='-')
plt.show()
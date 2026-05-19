import numpy as np
import matplotlib.pyplot as plt

# 设置全局字体为Arial
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial']

# 1. 定义数据点
num_tags = np.logspace(3, 5, 10)
data = {
    'original': np.array([1.32, 1.30, 1.28, 1.25, 1.22, 1.20, 1.20, 1.21, 1.22, 1.24]),
    'reusedMean': np.array([0.85, 0.82, 0.80, 0.79, 0.78, 0.78, 0.80, 0.82, 0.85, 0.88]),
    'mergedQE': np.array([0.65, 0.64, 0.63, 0.63, 0.62, 0.62, 0.62, 0.63, 0.64, 0.65]),
    'turnedFNS': np.array([1.28, 1.25, 1.22, 1.19, 1.16, 1.14, 1.13, 1.14, 1.15, 1.17]),
    'mergedFNS': np.array([1.05, 1.02, 1.00, 0.97, 0.94, 0.92, 0.93, 0.95, 0.98, 1.02]),
    'turnedSEM': np.array([1.31, 1.29, 1.27, 1.24, 1.21, 1.19, 1.19, 1.20, 1.21, 1.23])
}

# 2. 计算OFP线的数据
improvement_rate = np.linspace(0.6346, 0.5856, len(num_tags))
data['OFP'] = data['original'] * (1 - improvement_rate)

# 3. 绘图
fig, ax = plt.subplots(figsize=(7, 5))

# 定义绘图样式
styles = {
    'original':   {'marker': 'o', 'color': 'blue', 'linestyle': '-', 'fillstyle': 'none'},
    'reusedMean': {'marker': 's', 'color': 'orange', 'linestyle': '-', 'fillstyle': 'none'},
    'mergedQE':   {'marker': 'd', 'color': 'brown', 'linestyle': '-', 'fillstyle': 'none'},
    'turnedFNS':  {'marker': 'v', 'color': 'green', 'linestyle': '-', 'fillstyle': 'none'},
    'mergedFNS':  {'marker': '^', 'color': '#000000', 'linestyle': '-', 'fillstyle': 'none'},
    'turnedSEM':  {'marker': 'x', 'color': 'red', 'linestyle': '-', 'markersize': 5, 'fillstyle': 'full'},
    'OFP':        {'marker': 'p', 'color': 'purple', 'linestyle': '--', 'linewidth': 2.5, 'fillstyle': 'full'}
}

# 修正图例顺序
plot_order = [
    'original', 'mergedQE', 'mergedFNS', 
    'reusedMean', 'turnedFNS', 'turnedSEM', 
    'OFP'
]

# 按照指定的顺序绘制每一条线
for label in plot_order:
    y_values = data[label]
    style_dict = styles[label]
    
    ax.plot(num_tags, y_values,
             label=label,
             marker=style_dict.get('marker'),
             color=style_dict.get('color'),
             linestyle=style_dict.get('linestyle'),
             linewidth=style_dict.get('linewidth', 1.5),
             markersize=style_dict.get('markersize', 9),
             mew=1.5,
             fillstyle=style_dict.get('fillstyle', 'full')
            )

# 设置图表属性
ax.set_xscale('log')
ax.set_xlabel('Number of tags', fontsize=16)
ax.set_ylabel('Total time (s)', fontsize=16)
ax.grid(True, which="major", ls="-", linewidth=0.5, color='lightgrey')

# 设置X轴和Y轴的刻度和范围
ax.set_xticks([10**3, 10**4, 10**5])
ax.set_xticklabels([r'$10^3$', r'$10^4$', r'$10^5$'])
ax.set_yticks([0.4, 0.8, 1.2, 1.6])
ax.set_ylim(0.4, 1.6)

ax.tick_params(axis='both', which='major', labelsize=16)
ax.legend(loc='upper right', ncol=2, fontsize=14, frameon=True)
fig.tight_layout(pad=0.5)

# 保存图表
plt.savefig('final_protocol_performance_final.pdf')

print("图表已按照您的最终要求生成，并保存为 'final_protocol_performance_final.pdf'")
# data/resources.py
# 课题组公开资源元数据（论文 + 代码/数据集）
# 按年份倒序排列（最新在前），前端会自动排序

ARTICLES = [
    {
        "id": "art_001",
        "title": "LightGen: A Fully-Optical Neural Network for Real-Time Inference",
        "authors": ["陈一彤", "李明", "王雪"],
        "venue": "Nature Photonics",
        "year": 2025,
        "abstract": "我们提出了 LightGen——首个全光神经网络，利用衍射深度学习实现图像分类的光速推理。该系统无需电子计算单元，在 MNIST 和 CIFAR-10 上分别达到 98.2% 和 76.5% 的准确率，推理延迟低于 1 纳秒。",
    },
    {
        "id": "art_002",
        "title": "Reconfigurable Silicon Photonic Circuits for On-Chip AI Acceleration",
        "authors": ["陈一彤", "张哲"],
        "venue": "Optica",
        "year": 2024,
        "abstract": "本文展示了一种可编程硅光子电路，基于马赫-曾德尔干涉仪（MZI）阵列，支持在线训练与动态权重更新。实验表明，该芯片可在 1550nm 波长下实现 1T MACs/s 的计算吞吐量，能效比 GPU 高两个数量级。",
    }
]

RESOURCES = [
    {
        "id": "res_001",
        "title": "LightGen Simulation Framework",
        "authors": ["陈一彤", "刘思琪"],
        "type": "Code",
        "year": 2025,
        "readme": "本仓库包含 LightGen 芯片的完整仿真框架，基于 PyTorch 和角谱法（Angular Spectrum Method）。支持前向模型、逆向设计、以及 MNIST/CIFAR-10 示例数据集。适合研究衍射光学神经网络的初学者与进阶用户。",
    },
    {
        "id": "res_002",
        "title": "D²NN Experimental Dataset (Optical MNIST)",
        "authors": ["李明", "陈一彤"],
        "type": "Dataset",
        "year": 2025,
        "readme": "该数据集包含我们在 D²NN 实验中采集的光学 MNIST 图像原始测量数据。包括输入平面光场、输出平面光强分布、以及对应的标签。数据格式为 .mat（MATLAB）和 .npz（NumPy），共 10,000 组样本。",
    }
]
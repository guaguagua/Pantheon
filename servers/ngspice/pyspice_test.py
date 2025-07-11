#!/usr/bin/env python3

import PySpice.Logging.Logging as Logging
logger = Logging.setup_logging()

from PySpice.Spice.Netlist import Circuit
from PySpice.Unit import *
import numpy as np

def test_ngspice():
    """
    创建一个简单的电阻分压器电路来测试ngspice
    电路：5V电源 -> R1(1kΩ) -> 节点n1 -> R2(2kΩ) -> 地
    """
    
    print("=== PySpice + ngspice 测试 ===")
    
    # 创建电路
    circuit = Circuit('简单分压器测试')
    
    # 添加电压源：5V直流电源
    circuit.V('supply', 'vin', circuit.gnd, 5@u_V)
    
    # 添加电阻：R1 = 1kΩ, R2 = 2kΩ
    circuit.R('1', 'vin', 'n1', 1@u_kΩ)  # R1: vin到n1
    circuit.R('2', 'n1', circuit.gnd, 2@u_kΩ)  # R2: n1到地
    
    print("电路网表:")
    print(circuit)
    print("\n" + "="*50)
    
    # 创建仿真器
    simulator = circuit.simulator(temperature=25, nominal_temperature=25)
    
    # 进行直流工作点分析
    print("执行直流工作点分析...")
    analysis = simulator.operating_point()
    
    # 显示结果 - 修复DeprecationWarning
    # 方法1: 使用 .item() 方法（推荐）
    vin_value = analysis['vin'].item()
    n1_value = analysis['n1'].item()
    
    print("\n仿真结果:")
    print(f"输入电压 (vin): {vin_value:.3f} V")
    print(f"分压点电压 (n1): {n1_value:.3f} V")
    
    # 理论计算验证
    theoretical_voltage = 5 * (2000 / (1000 + 2000))  # 分压公式
    print(f"理论分压值: {theoretical_voltage:.3f} V")
    
    # 验证结果
    error = abs(n1_value - theoretical_voltage)
    
    if error < 0.001:  # 误差小于1mV
        print("✅ ngspice工作正常！仿真结果与理论值一致")
        return True
    else:
        print(f"❌ 仿真结果有误差: {error:.6f} V")
        return False

def test_dc_sweep():
    """
    进行DC扫描测试
    """
    print("\n=== DC扫描测试 ===")
    
    circuit = Circuit('DC扫描测试')
    
    # 可变电压源
    circuit.V('supply', 'vin', circuit.gnd, 5@u_V)
    circuit.R('1', 'vin', 'n1', 1@u_kΩ)
    circuit.R('2', 'n1', circuit.gnd, 2@u_kΩ)
    
    simulator = circuit.simulator(temperature=25, nominal_temperature=25)
    
    # DC扫描：从0V到5V，步长0.5V
    print("执行DC扫描分析 (0V到5V)...")
    analysis = simulator.dc(Vsupply=slice(0, 5, 0.5))
    
    print("\nDC扫描结果:")
    print("输入电压(V) -> 输出电压(V)")
    
    # 修复DeprecationWarning - 遍历数组元素
    for i in range(len(analysis.Vsupply)):
        # vin = analysis.Vsupply[i]
        vin = analysis['vin'][i]
        vout = analysis['n1'][i]
        print(f"{float(vin):8.1f} -> {float(vout):8.3f}")
    
    return True

# 另一种处理方法的示例函数
def safe_extract_value(analysis_result, node_name):
    """
    安全地从分析结果中提取标量值
    """
    value = analysis_result[node_name]
    
    if hasattr(value, 'item'):
        # 如果是NumPy数组，使用.item()方法
        return value.item()
    elif hasattr(value, '__len__') and len(value) == 1:
        # 如果是长度为1的序列，提取第一个元素
        return float(value[0])
    else:
        # 否则直接转换
        return float(value)

if __name__ == "__main__":
    try:
        # 基本工作点测试
        success1 = test_ngspice()
        
        # DC扫描测试
        success2 = test_dc_sweep()
        
        if success1 and success2:
            print("\n🎉 所有测试通过！ngspice安装和配置正确。")
        else:
            print("\n⚠️  部分测试失败，请检查ngspice安装。")
            
    except Exception as e:
        print(f"\n❌ 测试失败！错误信息: {e}")
        print("\n可能的问题:")
        print("1. ngspice未正确安装")
        print("2. PySpice配置问题") 
        print("3. 系统PATH环境变量问题")

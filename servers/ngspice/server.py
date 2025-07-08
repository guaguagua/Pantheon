import matplotlib.pyplot as plt
import numpy as np

# 导入 PySpice 相关库
from PySpice.Spice.Netlist import Circuit
from PySpice.Unit import *

# 1. 创建电路
circuit = Circuit('RC Low-Pass Filter')

# 2. 添加元器件
#    - V1: 0V 到 5V 的脉冲电压源
#    - R1: 1kΩ 电阻
#    - C1: 1μF 电容
circuit.PulseVoltageSource('input', 'in', circuit.gnd,
                         initial_value=0@u_V, pulsed_value=5@u_V,
                         delay_time=1@u_ms, rise_time=1@u_ns, fall_time=1@u_ns,
                         pulse_width=5@u_ms, period=10@u_ms)
circuit.R(1, 'in', 'out', 1@u_kOhm)
circuit.C(1, 'out', circuit.gnd, 1@u_uF)

# 3. 创建仿真器
simulator = circuit.simulator(temperature=25, nominal_temperature=25)

# 4. 进行瞬态分析
#    - 仿真步长: 10μs
#    - 仿真总时长: 20ms
analysis = simulator.transient(step_time=10@u_us, end_time=20@u_ms)

# 5. 访问并绘制结果
time = np.array(analysis.time)
input_voltage = np.array(analysis['in'])
output_voltage = np.array(analysis.out)

plt.figure(figsize=(10, 6))
plt.title('RC Low-Pass Filter Transient Analysis')
plt.xlabel('Time (s)')
plt.ylabel('Voltage (V)')
plt.grid()
plt.plot(time, input_voltage, label='V(in)')
plt.plot(time, output_voltage, label='V(out)')
plt.legend()
plt.show()

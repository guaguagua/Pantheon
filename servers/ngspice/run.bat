@echo off
echo Starting NGSpice Op-Amp Simulation...
ngspice -b opamp_sim.cir -o simulation.log 
echo Simulation completed. Check simulation.log for details.
dir *.txt

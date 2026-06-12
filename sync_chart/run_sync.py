import sys
sys.path.insert(0, '/home/node/.local/lib/python3.11/site-packages')

# Now import and run the actual script
import runpy
sys.argv[0] = '/home/userdata/home/Code/Olares_Project/Olares_Agent_Scripts/sync_chart/sync_chart.py'
runpy.run_path('/home/userdata/home/Code/Olares_Project/Olares_Agent_Scripts/sync_chart/sync_chart.py', run_name='__main__')

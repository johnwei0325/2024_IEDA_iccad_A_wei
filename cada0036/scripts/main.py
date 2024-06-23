import json
import random
import subprocess
import os
import argparse
import itertools
import signal
import time

# Define a timeout handler
def timeout_handler(signum, frame):
    raise TimeoutError("The operation timed out")

# Set the timeout limit (in seconds)
TIMEOUT = 9000  # Set to 2.5 hours (9000 seconds)

def parse_arguments():
    parser = argparse.ArgumentParser(description='CAD Contest Optimizer')
    parser.add_argument('-netlist', required=True, help='Path to the netlist file (design.v)')
    parser.add_argument('-library', required=True, help='Path to the library file (lib.json)')
    parser.add_argument('-cost_function', required=True, help='Path to the cost function executable')
    parser.add_argument('-output', required=True, help='Path to the output file (optimized_design.v)')
    return parser.parse_args()

def convert_netlist_to_abc_format(original_netlist):
    output_filename = 'release/net_m/design_m.v'
    convert_command = f'python3 scripts/convert_netlist_1.py {original_netlist} {output_filename}'
    result = subprocess.run(convert_command, shell=True)
    if result.returncode != 0:
        print("Error running convert_netlist_1.py")
        return None
    return output_filename

def generate_random_genlib(data, iteration):
    attributes = data['information']['attributes']
    num_attributes = len(attributes)
    num_required_attributes = 7

    genlib_filename = 'release/genlib/lib.genlib'

    with open(genlib_filename, 'w') as genlib_file:
        for cell in data['cells']:
            cell_name = cell['cell_name']
            cell_type = cell['cell_type']


            cell_attributes = [float(cell[attr]) for attr in attributes if attr not in ['cell_name', 'cell_type']]
            while len(cell_attributes) < num_required_attributes:
                cell_attributes.append(1.0)

            # Generate permutations and select the one corresponding to the current iteration
            all_permutations = list(itertools.permutations(cell_attributes))
            chosen_permutation = all_permutations[iteration % len(all_permutations)]

            random.shuffle(cell_attributes)

            area = cell_attributes[0]
            rise_block_delay = cell_attributes[1]
            fall_block_delay = cell_attributes[2]
            rise_fanout_delay = cell_attributes[3]
            fall_fanout_delay = cell_attributes[4]
            input_load = cell_attributes[5]
            max_load = cell_attributes[6]


            if cell_type == "and":
                function = "Y=A*B"
            elif cell_type == "or":
                function = "Y=A+B"
            elif cell_type == "xor":
                function = "Y=A*!B+!A*B"
            elif cell_type == "nand":
                function = "Y=!(A*B)"
            elif cell_type == "nor":
                function = "Y=!(A+B)"
            elif cell_type == "not":
                function = "Y=!A"
            elif cell_type == "buf":
                function = "Y=A"
            elif cell_type == "xnor":
                function = "Y=!(A^B)"
            else:
                continue


            genlib_file.write(f"GATE {cell_name} {area} {function};\n")
            genlib_file.write(f"    PIN * NONINV {input_load} {max_load} {rise_block_delay} {rise_fanout_delay} {fall_block_delay} {fall_fanout_delay}\n")

    return genlib_filename

def create_abc_script(inputfile, genlib_filename):
    netlist_input = inputfile
    netlist_output = 'release/net_mapped/netlist_mapped.v'
    abc_script = f"""
    read {netlist_input}
    strash
    
    balance
    rewrite
    rewrite -z
    resub
    refactor
    balance
    fraig
    compress
    b
    resyn
    resyn2
    resyn3
    dc2
    compress2
    dch
    dc2
    
    read_library {genlib_filename}
    map
    
    write_verilog {netlist_output}
    """
    script_filename = 'release/optimize.abc'
    with open(script_filename, 'w') as file:
        file.write(abc_script)
    #print(f"{script_filename} generated...")
    return script_filename, netlist_output

def run_abc_script(script_filename):
    abc_command = f'./abc/abc -f {script_filename}'
    result = subprocess.run(abc_command, shell=True, cwd='.')
    if result.returncode != 0:
        print("Error running abc script")
        return False
    return True


def convert_netlist_to_output_format(mapped_netlist):
    output_filename = 'release/net_complete/converted_design.v'
    convert_command = f'python3 scripts/convert_netlist_2.py {mapped_netlist} {output_filename}'
    result = subprocess.run(convert_command, shell=True)
    if result.returncode != 0:
        print("Error running convert_netlist_2.py")
        return None
    return output_filename

def estimate_cost(netlist_filename, library_path, cost_function):
    cost_output_filename = f'release/cost.txt'
    cost_command = f'{cost_function} -library {library_path} -netlist {netlist_filename} -output {cost_output_filename}'
    result = subprocess.run(cost_command, shell=True)
    if result.returncode != 0:
        print("Error running cost estimator")
        return None

    cost_output_path = cost_output_filename
    with open(cost_output_path, 'r') as file:
        output_line = file.readline().strip()  # 假设输出文件只有一行
        if "cost = " in output_line:
            cost_str = output_line.split('=')[1].strip()
            cost = float(cost_str)
        else:
            print("Unexpected output format in cost file")
            return None

    return cost


def main():
    # Record the start time
    start_time = time.time()
    
    # Set the timeout signal
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(TIMEOUT)

    try:
        args = parse_arguments()

        with open(args.library, 'r') as f:
            data = json.load(f)

        num_iterations = 3000
        best_cost = float('inf')
        best_netlist = args.output
        best_genlib = 'best_genlib.genlib'

        converted_netlist_abc = convert_netlist_to_abc_format(args.netlist)
        if not converted_netlist_abc:
            print("Failed to convert netlist to ABC format")
            return

        for iteration in range(num_iterations):
            print(f"Iteration {iteration + 1}")
            genlib_filename = generate_random_genlib(data, iteration)
            script_filename, mapped_netlist = create_abc_script(converted_netlist_abc, genlib_filename)

            if not run_abc_script(script_filename):
                print(f"Skipping iteration {iteration + 1} due to abc script error")
                continue

            converted_netlist = convert_netlist_to_output_format(mapped_netlist)
            if not converted_netlist:
                print(f"Skipping iteration {iteration + 1} due to conversion error")
                continue

            current_cost = estimate_cost(converted_netlist, args.library, args.cost_function)
            if current_cost is None:
                print(f"Skipping iteration {iteration + 1} due to cost estimation error")
                continue

            print(f"Cost for iteration {iteration + 1}: {current_cost}")

            if current_cost < best_cost:
                best_cost = current_cost
                os.rename(converted_netlist, best_netlist)
                os.rename(genlib_filename, best_genlib)
                print(f"\033[91mNew best netlist found: {best_netlist} with cost {best_cost} and genlib {best_genlib}\033[0m")

        print(f"\033[92mBest netlist saved as {best_netlist} with cost {best_cost}\033[0m")
        print(f"\033[92mBest genlib saved as {best_genlib}\033[0m")
        
    except TimeoutError as e:
        print(str(e))
    finally:
        # Record the end time and calculate the elapsed time
        end_time = time.time()
        elapsed_time = end_time - start_time
        print(f"Elapsed time: {elapsed_time:.2f} seconds")
        signal.alarm(0)  # Disable the alarm
    

if __name__ == "__main__":
    main()

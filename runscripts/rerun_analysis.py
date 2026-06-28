import os
import subprocess


def clean_analysis_output(outdir):
    """Remove existing analysis output to allow fresh analysis"""
    metadata_file = os.path.join(outdir, "metadata.json")
    if os.path.exists(metadata_file):
        print(f"Removing existing analysis output in {outdir}")
        # Remove metadata.json and the variant-specific output directory
        os.remove(metadata_file)
        variant_dir = os.path.join(outdir, "variant=0")
        if os.path.exists(variant_dir):
            import shutil

            shutil.rmtree(variant_dir)


def run_analysis():
    # Parameters for the analysis
    config_file = "configs/adhesin_RNA.json"
    outdir = "out/adhesins_RNA"
    exp_id = "adhesins_RNA"
    variant_data_dir = "out/adhesins_RNA/variant_sim_data"

    # Clean up existing analysis output
    clean_analysis_output(outdir)

    # Build the command using the virtual environment's Python
    venv_python = os.path.join(os.getcwd(), ".venv", "bin", "python")
    cmd = [
        venv_python,
        "runscripts/analysis.py",
        "--config",
        config_file,
        "--outdir",
        outdir,
        "--experiment_id",
        exp_id,
        "--variant_data_dir",
        variant_data_dir,
        "--validation_data_path",
        "--variant",
        "0",
        "--lineage_seed",
        "0",
        "--generation",
        "1",
        "--agent_id",
        "0",
    ]

    # Run the command
    print("Running analysis...")
    result = subprocess.run(cmd, check=True)

    if result.returncode == 0:
        print(f"\nAnalysis complete! Output saved to: {outdir}")
    else:
        print("\nAnalysis failed!")


36

if __name__ == "__main__":
    run_analysis()

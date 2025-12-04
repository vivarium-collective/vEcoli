import marimo

__generated_with = "0.17.7"
app = marimo.App(width="full")


@app.cell
def _():
    import polars as pl

    prot_fp = "/Users/alexanderpatrie/sms/vecoli_fork/out/sms_multiseed_multigen/analyses/experiment_id=sms_multiseed_multigen/variant=0/ptools_proteins.txt"
    return pl, prot_fp


@app.cell
def _(pl, prot_fp):
    pl.read_csv(prot_fp)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

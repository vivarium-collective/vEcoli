import marimo

__generated_with = "0.17.7"
app = marimo.App(width="full")


@app.cell
def _():
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    fp = "configs/single_cell.json"
    sim = EcoliSim.from_file(fp)
    return (sim,)


@app.cell
def _(sim):
    sim.max_duration = 22.0
    return


@app.cell
def _(sim):
    sim.build_ecoli()
    return


@app.cell
def _(sim):
    sim.run()
    return


@app.cell
def _(sim):
    d = sim.query()
    return (d,)


@app.cell
def _(d):
    type(d)
    return


@app.cell
def _(d):
    d.keys()
    return


@app.cell
def _(d):
    for v in d:
        print(d[v].keys())
    return


@app.cell
def _(sim):
    sim.ecoli_experiment.update()
    return


if __name__ == "__main__":
    app.run()

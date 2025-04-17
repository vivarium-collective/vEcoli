nextflow.enable.dsl=2

// Declare input param
params.config = null

process runBigraphWorkflow {
    input:
    path config_file

    output:
    path("*.json")

    script:
    """
    python bigraph_workflow.py ${config_file}
    """
}

workflow {
    if (!params.config) {
        error "Missing required parameter: --config <path to config JSON>"
    }

    runBigraphWorkflow(config_file: file(params.config))
}

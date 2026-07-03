# Here, the code for the following logic should be implemented.
# * It should read over all the results file created (under 'preprocess_job/results/') from each job units.
# * Every tool will differ, but logic to combine all the results into single file is needed.
#     * This file should be saved under 'postprocess_job/aggregated_result.json'.

def run(**kwargs):
    # ADD CODE LOGIC TO COMBINE RESULTS.
    # Aggregate the per-job results into one standard envelope, then serialize.
    # postprocess output defaults to json (the aggregated envelope carries
    # metadata that tsv can't represent); pass -f tsv to force tsv. Example:
    #
    #   from core.result_writer import write_results
    #   envelope = {"warnings": [], "errors": [], "results": [ ... ]}
    #   write_results(envelope,
    #                 output_prefix=kwargs.get("output_prefix"),
    #                 output_format=kwargs.get("output_format") or "json")
    pass
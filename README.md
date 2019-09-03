# reticulatus
A long snake for a long assemblies

## How to drive this thing

#### Setup the environment

```
conda env create --name reticulatus --file environments/base.yaml
conda activate reticulatus
```

Initalise which pipeline you want. It will almost always be `Snakefile-base` for the time being. Run `Snakefile-full`to replicate our mock community pipeline.

```
cp Snakefile-base Snakefile
```

#### Write your configuation

```
cp config.yaml.example config.yaml
```

Replace the YAML keys as appropriate. Keys are:

* `dehumanizer_database_root` empty directory in which to download the dehumanizer references (requires ~8.5GB)
* `kraken2_database_root` path to pre-built kraken2 database (*i.e.* the directory containing the `.k2d` files), or the path to a directory in which to `wget` a copy of our 30GB usual database. If the database already exists, you **must** `touch k2db.ok` in this directory or **bad things** will happen
* `slack_token` if you want to be bombarded with slack messages regarding the success and failure of your snakes, insert a suitable bot API token here
* `slack_channel` if using a `slack_token`, enter the name of the channel to send messages, including the leading `#`
* `cuda` set to `False` if you do not want GPU-acceleration and `True` if you have the means to go very fast
* `medaka_env` path to a singularity image (simg) or sandbox container to run medaka (GPU)

#### Tell reticulatus about your reads

```
cp reads.cfg.example reads.cfg
```

For each sample you have, add a tab delimited line with the following fields: 

* `sample_name` (make it unique, its used as a key to refer to later),
* `ont` (path to your long reads for this sample), 
* `i0` (path to your single-pair short reads for this sample, otherwise you can just set to `-`), 
* `i1` and `i2` (paths to your left and right paired end reads). If you only have long reads, set all the `i*` fields to `-`.

**Important** If you're using the GPU, you must ensure these directories are bound to the singularity container with `-B` in `--singularity-args`, use the same path for inside as outside to make things easier.


#### Tell reticulatus about your plans

```
cp manifest.cfg.example manifest.cfg
```

For each pipe you want to run, add a tab delimited line with the following fields:

* `uuid` a unique identifier, it can be anything, it will be used as a prefix for every file generated by this pipe
* `repolish` set to `-`
* `samplename` the reads to use, must be a key from the `reads.cfg`
* `callmodel`, `extraction`, `community` and `platform` are legacy options that can be set to `-` for now
* `spell` corresponds to a configuration in `spellbook.py`, this is where program versions and parameters are set
* `polishpipe` the polishing strategy, strategies are of the format <program>-<readtype>-<iterations> and are chained with the `.` character. *e.g.* `racon-ont-4.medaka-ont-1.pilon-ill-1` will perform four rounds of iterative `racon` long-read polishing, followed by one round of medaka long-read polishing and finally one round of `pilon` short-read polishing. Currently the following polishers are supported: racon, medaka, pilon and dehumanizer.
* `medakamodel` the option passed to `-m` for `medaka_consensus`

#### Engage the pipeline

Run the pipeline with `snakemake`, you **must** specify `--use-conda` to ensure that
any tools that require a special jail (*e.g.* for `python2`) are run far, far away
from everything else.
Additionally you **must** specify `--use-singularity` to use containers **and** provide suitable `--singularity-args` to use the GPU and bind directories.
Set `j` to the highest number of processes that you can fill with snakes before
your computer falls over.

```
snakemake -j <available_threads> --reason --use-conda --use-singularity --singularity-args '--nv -B <dir_inside>:<dir_outside>' -k --restart-times 1
```

## Housekeeping

Unless otherwise stated by a suitable header, the files within this repository are made available under the MIT license. If you use this pipeline, an acknowledgement in your work would be nice... Don't forget to [cite Snakemake](https://snakemake.readthedocs.io/en/stable/project_info/citations.html).

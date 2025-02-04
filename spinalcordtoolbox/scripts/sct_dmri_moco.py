#!/usr/bin/env python
#########################################################################################
#
# Motion correction of dMRI data.
#
# Inspired by Xu et al. Neuroimage 2013.
#
# Details of the algorithm:
# - grouping of DW data only (every n volumes, default n=5)
# - average all b0
# - average DWI data within each group
# - average DWI of all groups
# - moco on DWI groups
# - moco on b=0, using target volume: last b=0
# - moco on all dMRI data
# _ generating b=0 mean and DWI mean after motion correction
#
# ---------------------------------------------------------------------------------------
# Copyright (c) 2013 Polytechnique Montreal <www.neuro.polymtl.ca>
# Authors: Karun Raju, Tanguy Duval, Julien Cohen-Adad
# Modified: 2014-08-15
#
# About the license: see the file LICENSE.TXT
#########################################################################################

# TODO: if -f, we only need two plots.
#       Plot 1: X params with fitted spline,
#       plot 2: Y param with fitted splines.
#       Each plot will have all Z slices (with legend Z=0, Z=1, ...)
#       and labels: y; translation (mm), xlabel: volume #. Plus add grid.


import sys
import os
from typing import Sequence

from spinalcordtoolbox.moco import ParamMoco, moco_wrapper
from spinalcordtoolbox.utils.sys import init_sct, set_loglevel
from spinalcordtoolbox.utils.shell import SCTArgumentParser, Metavar, ActionCreateFolder, list_type, display_viewer_syntax
from spinalcordtoolbox.reports.qc import generate_qc


def get_parser():

    # initialize parameters
    param_default = ParamMoco(is_diffusion=True, group_size=3, metric='MI', smooth='1')

    parser = SCTArgumentParser(
        description="Motion correction of dMRI data. Some of the features to improve robustness were proposed in Xu et "
                    "al. (http://dx.doi.org/10.1016/j.neuroimage.2012.11.014) and include:\n"
                    "  - group-wise (-g)\n"
                    "  - slice-wise regularized along z using polynomial function (-param). For more info about the "
                    "method, type: isct_antsSliceRegularizedRegistration\n"
                    "  - masking (-m)\n"
                    "  - iterative averaging of target volume\n"
    )

    mandatory = parser.add_argument_group("\nMANDATORY ARGUMENTS")
    mandatory.add_argument(
        '-i',
        metavar=Metavar.file,
        required=True,
        help="Diffusion data. Example: dmri.nii.gz"
    )
    mandatory.add_argument(
        '-bvec',
        metavar=Metavar.file,
        required=True,
        help='Bvecs file. Example: bvecs.txt'
    )

    optional = parser.add_argument_group("\nOPTIONAL ARGUMENTS")
    optional.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show this help message and exit."
    )
    optional.add_argument(
        '-bval',
        metavar=Metavar.file,
        default=param_default.fname_bvals,
        help='Bvals file. Example: bvals.txt',
    )
    optional.add_argument(
        '-bvalmin',
        type=float,
        metavar=Metavar.float,
        default=param_default.bval_min,
        help='B-value threshold (in s/mm2) below which data is considered as b=0. Example: 50.0',
    )
    optional.add_argument(
        '-g',
        type=int,
        metavar=Metavar.int,
        default=param_default.group_size,
        help='Group nvols successive dMRI volumes for more robustness. Example: 2',
    )
    optional.add_argument(
        '-m',
        metavar=Metavar.file,
        default=param_default.fname_mask,
        help='Binary mask to limit voxels considered by the registration metric. Example: dmri_mask.nii.gz',
    )
    optional.add_argument(
        '-param',
        metavar=Metavar.list,
        type=list_type(',', str),
        help=f"Advanced parameters. Assign value with \"=\", and separate arguments with \",\".\n"
             f"  - poly [int]: Degree of polynomial function used for regularization along Z. For no regularization "
             f"set to 0. Default={param_default.poly}.\n"
             f"  - smooth [mm]: Smoothing kernel. Default={param_default.smooth}.\n"
             f"  - metric {{MI, MeanSquares, CC}}: Metric used for registration. Default={param_default.metric}.\n"
             f"  - gradStep [float]: Searching step used by registration algorithm. The higher the more deformation "
             f"allowed. Default={param_default.gradStep}.\n"
             f"  - sample [None or 0-1]: Sampling rate used for registration metric. "
             f"Default={param_default.sampling}.\n"
    )
    optional.add_argument(
        '-x',
        choices=['nn', 'linear', 'spline'],
        default=param_default.interp,
        help="Final interpolation."
    )
    optional.add_argument(
        '-ofolder',
        metavar=Metavar.folder,
        action=ActionCreateFolder,
        default=param_default.path_out,
        help="Output folder. Example: dmri_moco_results"
    )
    optional.add_argument(
        "-r",
        choices=('0', '1'),
        default=param_default.remove_temp_files,
        help="Remove temporary files. 0 = no, 1 = yes"
    )
    optional.add_argument(
        '-v',
        metavar=Metavar.int,
        type=int,
        choices=[0, 1, 2],
        default=1,
        # Values [0, 1, 2] map to logging levels [WARNING, INFO, DEBUG], but are also used as "if verbose == #" in API
        help="Verbosity. 0: Display only errors/warnings, 1: Errors/warnings + info messages, 2: Debug mode"
    )
    optional.add_argument(
        '-qc',
        metavar=Metavar.folder,
        action=ActionCreateFolder,
        help="The path where the quality control generated content will be saved. (Note: "
             "Both '-qc' and '-qc-seg' are required in order to generate a QC report.)"
    )
    optional.add_argument(
        '-qc-seg',
        metavar=Metavar.file,
        help="Segmentation of spinal cord to improve cropping in qc report. (Note: "
             "Both '-qc' and '-qc-seg' are required in order to generate a QC report.)"
    )
    optional.add_argument(
        '-qc-fps',
        metavar=Metavar.float,
        type=float,
        default=3,
        help="This float number is the number of frames per second for the output gif images."
    )
    optional.add_argument(
        '-qc-dataset',
        metavar=Metavar.str,
        help="If provided, this string will be mentioned in the QC report as the dataset the process was run on."
    )
    optional.add_argument(
        '-qc-subject',
        metavar=Metavar.str,
        help="If provided, this string will be mentioned in the QC report as the subject the process was run on."
    )

    return parser


def main(argv: Sequence[str]):
    parser = get_parser()
    arguments = parser.parse_args(argv)
    verbose = arguments.v
    set_loglevel(verbose=verbose)

    # initialization
    param = ParamMoco(is_diffusion=True, group_size=3, metric='MI', smooth='1')

    # Fetch user arguments
    param.fname_data = arguments.i
    param.fname_bvecs = arguments.bvec
    param.fname_bvals = arguments.bval
    param.bval_min = arguments.bvalmin
    param.group_size = arguments.g
    param.fname_mask = arguments.m
    param.interp = arguments.x
    param.path_out = arguments.ofolder
    param.remove_temp_files = arguments.r
    if arguments.param is not None:
        param.update(arguments.param)

    path_qc = arguments.qc
    qc_fps = arguments.qc_fps
    qc_dataset = arguments.qc_dataset
    qc_subject = arguments.qc_subject
    qc_seg = arguments.qc_seg

    mutually_inclusive_args = (path_qc, qc_seg)
    is_qc_none, is_seg_none = [arg is None for arg in mutually_inclusive_args]
    if not (is_qc_none == is_seg_none):
        parser.error("Both '-qc' and '-qc-seg' are required in order to generate a QC report.")

    # run moco
    fname_output_image = moco_wrapper(param)

    set_loglevel(verbose)  # moco_wrapper changes verbose to 0, see issue #3341

    # QC report
    if path_qc is not None:
        generate_qc(fname_in1=fname_output_image, fname_in2=param.fname_data, fname_seg=qc_seg,
                    args=argv, path_qc=os.path.abspath(path_qc), fps=qc_fps, dataset=qc_dataset,
                    subject=qc_subject, process='sct_dmri_moco')

    display_viewer_syntax([fname_output_image, param.fname_data], mode='ortho,ortho', verbose=verbose)


if __name__ == "__main__":
    init_sct()
    main(sys.argv[1:])

#!/usr/bin/env bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
GEF_DIR=${SCRIPT_DIR}/..
TEST_DIR="${GEF_DIR}/testing_gef.tmp"
LISTING=${TEST_DIR}/listing.txt

CART_CONFIG_ORIG=${GEF_DIR}/config/gaussian_bubble.ini
CUBE_CONFIG_ORIG=${GEF_DIR}/config/dcmip31.ini
CONFIG_BASE=${TEST_DIR}/config.ini

do_2d=1
do_3d=1
do_precond=0

mkdir -pv ${TEST_DIR}
rm -rf ${TEST_DIR}/*

function set_param() {
    file=${1}
    shift 1
    for arg in $@; do
        # echo "${arg} = ${!arg}"
        # sed_str='s/^'${arg}'\s*=\s*.*/'${arg}' = '${!arg}'/'
        # echo "sed str: ${sed_str}"
        sed -i ${file} -e 's|^'${arg}'\s*=\s*.*|'${arg}' = '${!arg}'|'
        # grep -q "^${arg}\s*=" ${file} || echo "${arg} = ${!arg}" >> ${file}
    done
}

function run_single_config() {
    cmd=${1}
    shift 1

    # Set up params
    cfg=${CONFIG_BASE}
    message="Testing: "
    for param in $@; do
        cfg=${cfg}.${param}
        message="${message} ${param}=${!param}"
    done
    cp ${CONFIG_BASE} ${cfg}
    set_param ${cfg} ${@}
    echo ${message}

    # Run test
    ${cmd} ${cfg} > ${LISTING} 2>&1 && return 0

    # In case of error, stop
    echo "AHHHHHhhhh error"
    cat ${LISTING}
    return 1
}

function run_single_cart2d() {
    run_single_config ${GEF_DIR}/main_gef.py ${@} || return 1
}

function run_single_cubesphere() {
    run_single_config "mpirun -n 6 ${GEF_DIR}/main_gef.py" ${@} || return 1
}

function test_cart2d() {
    dt=5
    t_end=5
    time_integrator=ros2
    tolerance=1e-6
    starting_step=0
    gmres_restart=30
    nbsolpts=4
    nb_elements_horizontal=5
    nb_elements_vertical=8
    preconditioner=none
    output_freq=1
    save_state_freq=1
    store_solver_stats=1
    output_dir=${TEST_DIR}

    cp ${CART_CONFIG_ORIG} ${CONFIG_BASE}
    set_param ${CONFIG_BASE} dt t_end time_integrator tolerance starting_step gmres_restart        \
                             nbsolpts nb_elements_horizontal nb_elements_vertical preconditioner   \
                             output_freq save_state_freq store_solver_stats output_dir


    time_integrators="epi2 epi3 epi_stiff3 epi_stiff4 srerk3 imex2 tvdrk3 ros2 rosexp2 partrosexp2 strang_epi2_ros2 strang_ros2_epi2"
    for time_integrator in ${time_integrators}; do
        run_single_cart2d time_integrator || return 1
    done

    if [ $do_precond -gt 0 ]; then
        time_integrator=ros2
        preconditioner=fv
        precond_tolerance=1e-1
        run_single_cart2d time_integrator preconditioner precond_tolerance || return 1

        preconditioner=fv-mg
        mg_smoother=erk1
        pseudo_cfl=1
        mg_solve_coarsest=0
        run_single_cart2d time_integrator preconditioner mg_smoother pseudo_cfl mg_solve_coarsest || return 1

        mg_smoother=erk3
        pseudo_cfl=5
        run_single_cart2d time_integrator preconditioner mg_smoother pseudo_cfl mg_solve_coarsest || return 1

        mg_smoother=exp
        exp_smoothe_spectral_radii=2
        run_single_cart2d time_integrator preconditioner mg_smoother exp_smoothe_spectral_radii || return 1
    fi

    # All tests were successful
    return 0
}

function test_cube_sphere() {
    dt=30
    t_end=30
    time_integrator=ros2
    tolerance=1e-7
    starting_step=0
    gmres_restart=200
    nbsolpts=4
    nb_elements_horizontal=4
    nb_elements_vertical=8
    preconditioner=none
    num_pre_smoothe=1
    num_post_smoothe=1
    mg_solve_coarsest=1
    output_freq=1
    save_state_freq=1
    store_solver_stats=1
    output_dir=${TEST_DIR}

    cp ${CUBE_CONFIG_ORIG} ${CONFIG_BASE}
    set_param ${CONFIG_BASE} dt t_end time_integrator tolerance starting_step gmres_restart        \
                             nbsolpts nb_elements_horizontal nb_elements_vertical preconditioner   \
                             output_freq save_state_freq store_solver_stats output_dir             \
                             num_pre_smoothe num_post_smoothe mg_solve_coarsest

    time_integrators="epi2 epi3 epi_stiff3 epi_stiff4 srerk3 tvdrk3 ros2"
    no_time_integrators="imex2 rosexp2 partrosexp2 strang_epi2_ros2 strang_ros2_epi2"
    for time_integrator in ${time_integrators}; do
        run_single_cubesphere time_integrator || return 1
    done

    if [ $do_precond -gt 0 ]; then
        time_integrator=ros2
        preconditioner=fv
        precond_tolerance=1e-1
        run_single_cubesphere time_integrator preconditioner precond_tolerance || return 1

        preconditioner=fv-mg
        mg_smoother=erk1
        pseudo_cfl=4e3
        run_single_cubesphere time_integrator preconditioner mg_smoother pseudo_cfl || return 1

        mg_smoother=erk3
        pseudo_cfl=11e4
        run_single_cubesphere time_integrator preconditioner mg_smoother pseudo_cfl || return 1

        mg_smoother=exp
        exp_smoothe_spectral_radii=2
        run_single_cubesphere time_integrator preconditioner mg_smoother exp_smoothe_spectral_radii || return 1
    fi

    # All tests were successful
    return 0
}

if [ $do_2d -gt 0 ]; then
    echo "2D Euler"
    test_cart2d || exit -1
fi

if [ $do_3d -gt 0 ]; then
    echo "3D Euler (cube sphere)"
    test_cube_sphere || exit -1
fi

echo "Test successful!" && rm -rf ${TEST_DIR}

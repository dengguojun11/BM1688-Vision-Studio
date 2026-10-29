from pathlib import Path, PurePosixPath

from labelu.scripts.bm1688_docker_model_quant import (
    build_search_qtable_command,
    detector_head_f16_qtable_lines,
    ensure_qtable_lines,
)


def test_build_search_qtable_command_matches_official_flow():
    command = build_search_qtable_command(
        mlir_path=PurePosixPath("/work/yolov11n.mlir"),
        calib_dir=PurePosixPath("/work/calibration"),
        input_num=128,
        inference_num=10,
        chip="bm1688",
        expected_cos=0.99,
        max_float_layers=5,
        quantize_method_list="kl,mse",
        pre_qtable=PurePosixPath("/work/yolov11n_pre_qtable"),
        qtable=PurePosixPath("/work/yolov11n_qtable"),
        cali_table=PurePosixPath("/work/yolov11n_cali_table"),
    )

    assert command == [
        "run_calibration.py",
        "/work/yolov11n.mlir",
        "--dataset",
        "/work/calibration",
        "--input_num",
        "128",
        "--inference_num",
        "10",
        "--chip",
        "bm1688",
        "--max_float_layers",
        "5",
        "--expected_cos",
        "0.99",
        "--quantize_method_list",
        "kl,mse",
        "--search",
        "search_qtable",
        "--pre_qtable",
        "/work/yolov11n_pre_qtable",
        "--quantize_table",
        "/work/yolov11n_qtable",
        "-o",
        "/work/yolov11n_cali_table",
    ]


def test_detector_head_qtable_protects_all_mlir_head_ops(tmp_path: Path):
    mlir = tmp_path / "yolov11n.mlir"
    head_ops = [
        "/model.23/cv3.0/cv3.0.2/Conv_output_0_Conv",
        "/model.23/cv3.1/cv3.1.2/Conv_output_0_Conv",
        "/model.23/cv3.2/cv3.2.2/Conv_output_0_Conv",
        "/model.23/dfl/conv/Conv_output_0_Conv",
        "/model.23/dfl/Reshape_output_0_Reshape",
        "/model.23/dfl/Transpose_output_0_Transpose",
        "/model.23/dfl/Softmax_output_0_Softmax",
        "/model.23/dfl/Reshape_1_output_0_Reshape",
        "/model.23/Sigmoid_output_0_Sigmoid",
        "/model.23/Concat_output_0_Concat",
        "/model.23/Sub_output_0_Sub",
        "/model.23/Mul_2_output_0_Mul",
        "output0_Transpose",
    ]
    lines = [
        f'%0 = "top.Conv"() : () -> tensor<1xf32> loc("{name}")'
        for name in head_ops
    ]
    lines.extend(
        (
            '%1 = "top.Conv"() : () -> tensor<1xf32> loc("/model.22/Conv_output_0_Conv")',
            '%2 = "top.Weight"() : () -> tensor<1xf32> loc("/model.23/weight")',
        )
    )
    mlir.write_text("\n".join(lines), encoding="utf-8")

    protected = detector_head_f16_qtable_lines(
        mlir, "/model.23/dfl/conv/Conv_output_0_Conv"
    )

    assert protected == [f"{name} F16" for name in head_ops]
    assert all("/model.22/" not in line for line in protected)
    assert all("/weight" not in line for line in protected)


def test_detector_head_qtable_resolves_tpu_mlir_location_aliases(tmp_path: Path):
    mlir = tmp_path / "yolov11n.mlir"
    head_ops = [
        "/model.23/cv2.0/cv2.0.0/conv/Conv_output_0_Conv",
        "/model.23/cv2.0/cv2.0.1/conv/Conv_output_0_Conv",
        "/model.23/cv2.0/cv2.0.2/Conv_output_0_Conv",
        "/model.23/cv3.0/cv3.0.0/conv/Conv_output_0_Conv",
        "/model.23/cv3.0/cv3.0.1/conv/Conv_output_0_Conv",
        "/model.23/cv3.0/cv3.0.2/Conv_output_0_Conv",
        "/model.23/dfl/Reshape_output_0_Reshape",
        "/model.23/dfl/Transpose_output_0_Transpose",
        "/model.23/dfl/Softmax_output_0_Softmax",
        "/model.23/Sigmoid_output_0_Sigmoid",
        "/model.23/Concat_output_0_Concat",
        "/model.23/Mul_2_output_0_Mul",
        "output0_Transpose",
    ]
    operation_lines = [
        f'%{index} = "top.Conv"() : () -> tensor<1xf32> loc(#loc{index})'
        for index in range(len(head_ops))
    ]
    alias_lines = [
        f'#loc{index} = loc("{name}")' for index, name in enumerate(head_ops)
    ]
    mlir.write_text(
        "\n".join(operation_lines + alias_lines), encoding="utf-8"
    )

    protected = detector_head_f16_qtable_lines(
        mlir, "/model.23/dfl/conv/Conv_output_0_Conv"
    )

    assert protected == [f"{name} F16" for name in head_ops]


def test_detector_head_qtable_follows_generic_exported_graph(tmp_path: Path):
    mlir = tmp_path / "yolov11n.mlir"
    mlir.write_text(
        "\n".join(
            [
                '%0 = "top.Weight"() : () -> tensor<1xf32> loc(#loc0)',
                '%1 = "top.Conv"(%input, %0) : (tensor<1xf32>, tensor<1xf32>) -> tensor<1xf32> loc(#loc1)',
                '%2 = "top.Sigmoid"(%1) : (tensor<1xf32>) -> tensor<1xf32> loc(#loc2)',
                '%3 = "top.Permute"(%2) : (tensor<1xf32>) -> tensor<1xf32> loc(#loc3)',
                '#loc0 = loc("model.23.cv2.0.2.weight")',
                '#loc1 = loc("conv2d_65_Conv")',
                '#loc2 = loc("sigmoid_Sigmoid")',
                '#loc3 = loc("output0_Transpose")',
            ]
        ),
        encoding="utf-8",
    )

    protected = detector_head_f16_qtable_lines(
        mlir, "/model.23/dfl/conv/Conv_output_0_Conv"
    )

    assert set(protected) == {
        "conv2d_65_Conv F16",
        "sigmoid_Sigmoid F16",
        "output0_Transpose F16",
    }
    assert "/model.23/Sigmoid_output_0_Sigmoid F16" not in protected


def test_ensure_qtable_lines_restores_all_missing_head_rules(tmp_path: Path):
    qtable = tmp_path / "yolov11n_qtable"
    qtable.write_text(
        "# generated by search_qtable\n/model.23/Sigmoid_output_0_Sigmoid F16\n",
        encoding="utf-8",
    )
    required = [
        "/model.23/Sigmoid_output_0_Sigmoid F16",
        "/model.23/dfl/Softmax_output_0_Softmax F16",
        "output0_Transpose F16",
    ]

    assert ensure_qtable_lines(qtable, required) == 2
    assert ensure_qtable_lines(qtable, required) == 0
    text = qtable.read_text(encoding="utf-8")
    assert text.count("/model.23/dfl/Softmax_output_0_Softmax F16") == 1
    assert text.count("output0_Transpose F16") == 1

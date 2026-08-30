# Functional tests

This directory contains tests that use a fixed JPEG image. They do not connect
to the ESP32 camera and do not modify the firmware.

## Ollama vision test

The default fixture is a 640x480 JPEG compressed at quality 20, matching the
current ESP32-S3 stream configuration. The test checks that the model is
installed, sends the fixed image to Ollama, verifies a completed non-empty
response, and reports client/API timing.

From WSL, with Ollama running on Windows:

```bash
python3 /home/lk/car/test/ollama_vision_latency.py
```

Run five warm measurements and include a cold run. The script explicitly
unloads the resident model before measuring the cold request:

```bash
python3 /home/lk/car/test/ollama_vision_latency.py --runs 5 --include-cold
```

The default endpoint is `http://127.0.0.1:11434`. Override it with
`--host` or the `OLLAMA_HOST` environment variable. Use `--image` to test a
different local JPEG without adding camera capture.

## Baidu Image Understanding test

This test uses the same fixed JPEG and asks the default question
`这个是什么？请简短回答。`. It follows Baidu's asynchronous API: obtain an
access token, submit an image-understanding task, and poll until the
description is ready. It reports token, submit, processing, polling, and total
latency. Credentials are read only from environment variables.

```bash
export BAIDU_API_KEY="your-api-key"
export BAIDU_SECRET_KEY="your-secret-key"
python3 /home/lk/car/test/baidu_image_understanding_test.py --runs 3
```

The script never prints the access token or either credential. Use
`--poll-interval`, `--deadline`, and `--question` to adjust the measurement.
If a key has been pasted into chat, source control, or a log, rotate it before
running this test.

The JSON request sends the raw Base64 image value. Although the documentation
mentions URL encoding, percent-encoding the JSON field caused error 216201 in
the live endpoint; URL encoding is still used for URL query parameters.

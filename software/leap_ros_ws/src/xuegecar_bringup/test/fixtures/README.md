# Recorded overlap fixture

The CDR files are unmodified ROS messages from
`/home/lk/car/data/rosbags/web_collision_debug`, relative bag time
18.389383208 s (`/scan_ts`) and 18.389433416 s (`/odometry/filtered`).
The recorded LaserScan has 360 rays in `laser_frame`. The measured static
transform to `base_link` is translation `(0.020, 0, 0.1055)` with no rotation.
Twenty rays lie just inside the measured body's front edge. This scan previously
prevented reverse motion, despite no points in the rear hard-stop buffer.

Tests refresh message timestamps and use stationary odom TF, retaining the real
scan geometry and measured velocity. They also inject rear and side hazards
into copies to verify that the escape exception does not hide approaching points.

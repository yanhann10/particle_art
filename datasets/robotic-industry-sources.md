# Robotic Industry Data Sources

Public datasets with rich spatial structure suitable for particle-art source material.
Concept: robot end-effector trajectories → attractor-style curves; factory LiDAR → real-world point clouds.

## Datasets

| Dataset | Type | Format | Access |
|---|---|---|---|
| **Open X-Embodiment** (Google DeepMind, 2023) | 1M+ robot manipulation demos, joint angles + end-effector 3D trajectories | HuggingFace / TFRecord | https://robotics-transformer-x.github.io |
| **Bridge Data v2** | Kitchen robot manipulation, real-world 3D trajectories | rosbag / HDF5 | https://rail.eecs.berkeley.edu/datasets/bridge_release |
| **LeRobot** (HuggingFace) | Standardized robot action/observation data, many morphologies | parquet | https://huggingface.co/lerobot |
| **KITTI** | Outdoor LiDAR point clouds (autonomous vehicle, industrial context) | .bin | https://www.cvlibs.net/datasets/kitti |
| **ROS public bags** | Factory floor sweeps, bin-picking LIDAR | rosbag | https://wiki.ros.org/Datasets |

## What to extract

- **End-effector (x,y,z)** at ~50Hz over thousands of episodes → dense 3D trajectory curves, kinematically constrained, non-random
- **Joint angle time series** → parametric curves in configuration space (less intuitive visually but mathematically interesting)
- **LiDAR .bin files** → point cloud with industrial-object silhouettes (like n8q Lucy statue but factory floor)

## Structural fit with existing aesthetics

- Trajectory stream → strange-attractor style curves (xs4/zs4 register but sourced from real industrial motion)
- Multi-episode overlay → differential-growth-like fill (episodes diverge/converge in space)
- Factory LiDAR → real-world point cloud with recognizable silhouette (n8q register)

## Lumen Prize concept note

**"Choreography of replacement"** framing (Legacy Futures track):
Robot arm trajectories encode movements originally captured from human workers, then the humans were removed.
The particle system exhumes those gestures. Position: the gestures exist; the bodies don't.

Lineage: Haacke (systems art) + Hito Steyerl (factory as art subject).
Risk: must be apparatus-as-argument, not just beautiful data viz (Salvaggio critique of Anadol applies).

## Quickstart (LeRobot — easiest format)

```python
from datasets import load_dataset
ds = load_dataset("lerobot/pusht", split="train")
# ds[0]['observation.state'] → [x, y] end-effector position
# ds[0]['action'] → [dx, dy] delta actions
# Stack episodes → trajectory point cloud ready for three.js
```

cmake_minimum_required(VERSION 3.0.2)
project(robot_settings)

find_package(catkin REQUIRED)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/visualize_nodes.py
  scripts/publish_interpolated_path.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch cfg paths
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

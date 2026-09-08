/*********************************************************************
 *
 * Software License Agreement (BSD License)
 *
 *  Copyright (c) 2008, 2013, Willow Garage, Inc.
 *  All rights reserved.
 *
 *  Redistribution and use in source and binary forms, with or without
 *  modification, are permitted provided that the following conditions
 *  are met:
 *
 *   * Redistributions of source code must retain the above copyright
 *     notice, this list of conditions and the following disclaimer.
 *   * Redistributions in binary form must reproduce the above
 *     copyright notice, this list of conditions and the following
 *     disclaimer in the documentation and/or other materials provided
 *     with the distribution.
 *   * Neither the name of Willow Garage, Inc. nor the names of its
 *     contributors may be used to endorse or promote products derived
 *     from this software without specific prior written permission.
 *
 *  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 *  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 *  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 *  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 *  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 *  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 *  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 *  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 *  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 *  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
 *  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 *  POSSIBILITY OF SUCH DAMAGE.
 *
 * Author: Eitan Marder-Eppstein
 *         David V. Lu!!
 *********************************************************************/
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <limits>
#include <utility>
#include <vector>

#include <boost/thread.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <tf2/exceptions.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.h>

#include <costmap_2d/path_boundry_layer.h>

PLUGINLIB_EXPORT_CLASS(costmap_2d::PathBoundryLayer, costmap_2d::Layer)

using costmap_2d::LETHAL_OBSTACLE;
using costmap_2d::NO_INFORMATION;

namespace costmap_2d
{
namespace
{
void cal8Point(CirclePoints& points, int x, int y)
{
  points.push_back({ x, y });
  points.push_back({ x, -y });
  points.push_back({ -x, -y });
  points.push_back({ -x, y });
  points.push_back({ y, x });
  points.push_back({ y, -x });
  points.push_back({ -y, -x });
  points.push_back({ -y, x });
}

void bresenhamCircle(CirclePoints& circle, int radius)
{
  int x = 0;
  int y = radius;
  cal8Point(circle, x, y);
  int d = 2 * radius;
  while (x <= y)
  {
    if (d > 0)
    {
      d = d - 4 * x - 6;
    }
    else
    {
      d = d - 4 * (x - y) - 10;
      y--;
    }
    x++;
    cal8Point(circle, x, y);
  }
}

struct PathPoint
{
  double x;
  double y;
  double radius;
};
}  // namespace

PathBoundryLayer::PathBoundryLayer()
{
  min_x_ = min_y_ = std::numeric_limits<double>::max();
  max_x_ = max_y_ = std::numeric_limits<double>::lowest();
  hierarchy_.reserve(1000);
  contours_.reserve(1000);
}

PathBoundryLayer::~PathBoundryLayer()
{
}

void PathBoundryLayer::onInitialize()
{
  boost::lock_guard<boost::recursive_mutex> lock(*getMutex());
  ros::NodeHandle nh("~/" + name_), g_nh;

  global_frame_ = layered_costmap_->getGlobalFrameID();
  default_value_ = NO_INFORMATION;
  current_ = true;
  matchSize();

  {
    boost::lock_guard<boost::recursive_mutex> lock_param(inflation_access_);
    nh.param("expect_update_rate", expect_update_rate_, -1.0);
    nh.param("inflation_radius", default_inflation_radius_, 1.0);
    if (nh.getParam("use_dynamic_radius", use_dynamic_radius_))
    {
      nh.param("max_inflation_radius", max_inflation_radius_, 1.0);
      max_inflation_radius_ = std::max(max_inflation_radius_, default_inflation_radius_);
    }
    else
    {
      max_inflation_radius_ = default_inflation_radius_;
    }
    default_cell_inflation_radius_ = cellDistance(default_inflation_radius_);
    max_cell_inflation_radius_ = cellDistance(max_inflation_radius_);
    computeCaches();
  }

  need_reinflation_ = true;
  path_received_ = false;

  nh.param("path_topic", path_topic_, std::string(""));
  path_sub_ = g_nh.subscribe(path_topic_, 1, &PathBoundryLayer::pathCB, this);

  MyReconfigureServer::CallbackType cb = boost::bind(&PathBoundryLayer::reconfigureCB, this, _1, _2);
  if (dsrv_ != nullptr)
  {
    dsrv_->clearCallback();
    dsrv_->setCallback(cb);
  }
  else
  {
    dsrv_ = std::make_shared<MyReconfigureServer>(nh);
    dsrv_->setCallback(cb);
  }
}

void PathBoundryLayer::pathCB(const nav_msgs::Path& path_msg)
{
  boost::lock_guard<boost::recursive_mutex> lock(path_access_);
  path_msg_ = path_msg;
  path_received_ = true;
  need_reinflation_ = true;
  last_receive_stamp_ = ros::Time::now();
  ROS_INFO("PathBoundryLayer receive new path %zu", path_msg_.poses.size());
}

void PathBoundryLayer::reconfigureCB(MyConfig& config, uint32_t level)
{
  (void)level;
  if (enabled_ != config.enabled)
  {
    enabled_ = config.enabled;
  }
  setInflationParameters(config.inflation_radius, config.max_inflation_radius, config.use_dynamic_radius);
}

void PathBoundryLayer::setInflationParameters(double default_radius, double max_radius, bool use_dynamic_radius)
{
  boost::lock_guard<boost::recursive_mutex> lock(inflation_access_);

  default_inflation_radius_ = default_radius;
  max_inflation_radius_ = std::max(max_radius, default_inflation_radius_);
  use_dynamic_radius_ = use_dynamic_radius;

  computeCaches();

  need_reinflation_ = true;
  ROS_INFO("PathBoundryLayer setInflation radius %.2f %u max %.2f %u dynamic %d",
           default_inflation_radius_, default_cell_inflation_radius_, max_inflation_radius_, max_cell_inflation_radius_,
           use_dynamic_radius_);
}

void PathBoundryLayer::getInflationParameters(double& default_radius, double& max_radius) const
{
  default_radius = default_inflation_radius_;
  max_radius = max_inflation_radius_;
}

void PathBoundryLayer::computeCaches()
{
  default_cell_inflation_radius_ = cellDistance(default_inflation_radius_);
  max_cell_inflation_radius_ = cellDistance(max_inflation_radius_);
  if (max_cell_inflation_radius_ == 0)
  {
    return;
  }

  map_radius_circles_.clear();
  for (unsigned int radius = 0; radius <= max_cell_inflation_radius_ + 2; radius++)
  {
    bresenhamCircle(map_radius_circles_[radius], static_cast<int>(radius));
  }
  ROS_INFO("PathBoundryLayer computeCaches %u %zu", max_cell_inflation_radius_, map_radius_circles_.size());
}

unsigned int PathBoundryLayer::GetIndex(double radius)
{
  if (radius <= 0.0)
  {
    return 1;
  }

  return std::min(max_cell_inflation_radius_, static_cast<unsigned int>(radius / resolution_) + 1);
}

void PathBoundryLayer::addCircle(int ox, int oy, double radius)
{
  const CirclePoints& points = map_radius_circles_[GetIndex(radius)];

  for (const auto& point : points)
  {
    const int mat_x = ox + point[0] + pad_for_mat;
    const int mat_y = oy + point[1] + pad_for_mat;

    ROS_ASSERT(mat_x >= 0 && mat_x < static_cast<int>(size_x_ + pad_for_mat * 2));
    ROS_ASSERT(mat_y >= 0 && mat_y < static_cast<int>(size_y_ + pad_for_mat * 2));
    mat_.at<unsigned char>(mat_y, mat_x) = 255;
  }
}

void PathBoundryLayer::findCountours()
{
  cv::findContours(mat_, contours_, hierarchy_, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_NONE);

  for (const auto& contour : contours_)
  {
    for (const auto& point : contour)
    {
      boundry_cells_.push_back(
        MapLocation{ static_cast<unsigned int>(point.x - pad_for_mat), static_cast<unsigned int>(point.y - pad_for_mat) });
    }
  }
}

void PathBoundryLayer::updateBounds(double robot_x, double robot_y, double robot_yaw, double* min_x, double* min_y,
                                    double* max_x, double* max_y)
{
  (void)robot_x;
  (void)robot_y;
  (void)robot_yaw;
  boost::lock_guard<boost::recursive_mutex> lock(inflation_access_);
  if (!enabled_ || (max_cell_inflation_radius_ == 0) || resolution_ <= 0.0)
  {
    return;
  }

  boost::unique_lock<boost::recursive_mutex> lock_path(path_access_);
  if (expect_update_rate_ > 0.0 && path_received_)
  {
    if ((ros::Time::now() - last_receive_stamp_).toSec() > 1.0 / expect_update_rate_)
    {
      path_msg_.poses.clear();
      need_reinflation_ = true;
    }
  }

  if (path_received_ && need_reinflation_)
  {
    const auto t_begin = std::chrono::high_resolution_clock::now();
    addExtraBounds(min_x_, min_y_, max_x_, max_y_);
    boundry_cells_.clear();
    need_reinflation_ = false;

    std::vector<PathPoint> global_path_points;
    min_x_ = min_y_ = std::numeric_limits<double>::max();
    max_x_ = max_y_ = std::numeric_limits<double>::lowest();

    if (path_msg_.poses.empty())
    {
      useExtraBounds(min_x, min_y, max_x, max_y);
      return;
    }

    geometry_msgs::TransformStamped path_to_global;
    try
    {
      path_to_global = tf_->lookupTransform(global_frame_, path_msg_.header.frame_id, ros::Time(0));
    }
    catch (tf2::TransformException& ex)
    {
      ROS_INFO_THROTTLE(10.0, "%s", ex.what());
      useExtraBounds(min_x, min_y, max_x, max_y);
      return;
    }

    double last_x = 0.0;
    double last_y = 0.0;
    bool has_last = false;
    for (const auto& pose_msg : path_msg_.poses)
    {
      const double x = pose_msg.pose.position.x;
      const double y = pose_msg.pose.position.y;

      if (has_last && std::hypot(x - last_x, y - last_y) < resolution_ * 2.0)
      {
        continue;
      }
      has_last = true;
      last_x = x;
      last_y = y;

      geometry_msgs::PoseStamped global_pose;
      tf2::doTransform(pose_msg, global_pose, path_to_global);

      const double gx = global_pose.pose.position.x;
      const double gy = global_pose.pose.position.y;
      max_x_ = std::max(max_x_, gx);
      max_y_ = std::max(max_y_, gy);
      min_x_ = std::min(min_x_, gx);
      min_y_ = std::min(min_y_, gy);
      global_path_points.push_back({ gx, gy, pose_msg.pose.position.z });
    }
    lock_path.unlock();

    if (min_x_ >= max_x_ || min_y_ >= max_y_)
    {
      ROS_WARN_THROTTLE(1.0, "updateBounds failed cause size [%.3f %.3f]-[%.3f %.3f]", min_x_, min_y_, max_x_, max_y_);
      useExtraBounds(min_x, min_y, max_x, max_y);
      return;
    }

    const double pad_radius = (max_cell_inflation_radius_ + 1) * resolution_;
    max_x_ += pad_radius;
    max_y_ += pad_radius;
    min_x_ -= pad_radius;
    min_y_ -= pad_radius;
    const unsigned int size_x = static_cast<unsigned int>((max_x_ - min_x_) / resolution_);
    const unsigned int size_y = static_cast<unsigned int>((max_y_ - min_y_) / resolution_);

    resizeMap(size_x, size_y, resolution_, min_x_, min_y_);
    boundry_to_global_cell_dx_ =
      static_cast<int>((origin_x_ - layered_costmap_->getCostmap()->getOriginX()) / resolution_);
    boundry_to_global_cell_dy_ =
      static_cast<int>((origin_y_ - layered_costmap_->getCostmap()->getOriginY()) / resolution_);
    mat_ = cv::Mat(size_y + pad_for_mat * 2, size_x + pad_for_mat * 2, CV_8UC1, cv::Scalar(0));

    unsigned int mx = 0;
    unsigned int my = 0;
    for (const auto& path_point : global_path_points)
    {
      if (!worldToMap(path_point.x, path_point.y, mx, my))
      {
        continue;
      }
      const double radius = use_dynamic_radius_ ? path_point.radius : default_inflation_radius_;
      addCircle(static_cast<int>(mx), static_cast<int>(my), radius);
    }
    findCountours();

    {
      boost::lock_guard<boost::recursive_mutex> lock_data(*getMutex());
      memset(costmap_, default_value_, size_x_ * size_y_);
      for (const auto& loc : boundry_cells_)
      {
        setCost(loc.x, loc.y, LETHAL_OBSTACLE);
      }
    }

    const size_t t_cost =
      std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t_begin).count();
    ROS_INFO("PathBoundryLayer reinflation path boundry, size %zu time cost %zu", boundry_cells_.size(), t_cost);
  }

  useExtraBounds(min_x, min_y, max_x, max_y);
  *min_x = std::min(min_x_, *min_x);
  *min_y = std::min(min_y_, *min_y);
  *max_x = std::max(max_x_, *max_x);
  *max_y = std::max(max_y_, *max_y);
  if (layered_costmap_->isRolling())
  {
    boundry_to_global_cell_dx_ =
      static_cast<int>((origin_x_ - layered_costmap_->getCostmap()->getOriginX()) / resolution_);
    boundry_to_global_cell_dy_ =
      static_cast<int>((origin_y_ - layered_costmap_->getCostmap()->getOriginY()) / resolution_);
  }
}

void PathBoundryLayer::updateCosts(costmap_2d::Costmap2D& master_grid, int min_i, int min_j, int max_i, int max_j)
{
  boost::lock_guard<boost::recursive_mutex> lock(inflation_access_);
  if (!enabled_ || (max_cell_inflation_radius_ == 0) || resolution_ <= 0.0)
  {
    return;
  }

  for (const auto& loc : boundry_cells_)
  {
    const int global_x = static_cast<int>(loc.x) + boundry_to_global_cell_dx_;
    const int global_y = static_cast<int>(loc.y) + boundry_to_global_cell_dy_;
    if (global_x < min_i || global_x >= max_i || global_y < min_j || global_y >= max_j)
    {
      continue;
    }

    master_grid.setCost(global_x, global_y, LETHAL_OBSTACLE);
  }
}

void PathBoundryLayer::matchSize()
{
  boost::lock_guard<boost::recursive_mutex> lock(inflation_access_);
  if (layered_costmap_->getCostmap()->getResolution() < 1e-6)
  {
    ROS_WARN("PathBoundryLayer matchSize invalid resolution %f", layered_costmap_->getCostmap()->getResolution());
    return;
  }
  resolution_ = layered_costmap_->getCostmap()->getResolution();
  computeCaches();

  boundry_to_global_cell_dx_ =
    static_cast<int>((origin_x_ - layered_costmap_->getCostmap()->getOriginX()) / resolution_);
  boundry_to_global_cell_dy_ =
    static_cast<int>((origin_y_ - layered_costmap_->getCostmap()->getOriginY()) / resolution_);

  need_reinflation_ = true;
  ROS_INFO("PathBoundryLayer matchSize %.2f %.2f", resolution_, 1.0 / resolution_);
}

}  // namespace costmap_2d

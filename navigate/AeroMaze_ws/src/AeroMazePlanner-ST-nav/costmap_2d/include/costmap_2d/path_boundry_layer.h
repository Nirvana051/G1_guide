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
#ifndef PATH_BOUNDRY_LAYER_H_
#define PATH_BOUNDRY_LAYER_H_

#include <ros/ros.h>
#include <costmap_2d/layer.h>
#include <costmap_2d/layered_costmap.h>
#include <costmap_2d/costmap_layer.h>
#include <costmap_2d/PathBoundryPluginConfig.h>
#include <dynamic_reconfigure/server.h>
#include <boost/thread.hpp>
#include <map>
#include <memory>
#include <nav_msgs/Path.h>
#include <opencv2/imgproc/imgproc.hpp>
#include <vector>

namespace costmap_2d {

    using CirclePoints = std::vector<std::vector<int>>;
    using MyConfig = costmap_2d::PathBoundryPluginConfig;
    using MyReconfigureServer = dynamic_reconfigure::Server<MyConfig>;

    class PathBoundryLayer : public CostmapLayer {
    public:
        PathBoundryLayer();

        PathBoundryLayer(const PathBoundryLayer&) = delete;

        PathBoundryLayer(PathBoundryLayer&&) = delete;

        PathBoundryLayer& operator=(const PathBoundryLayer&) = delete;

        PathBoundryLayer& operator=(PathBoundryLayer&&) = delete;

        virtual ~PathBoundryLayer();

        virtual void onInitialize();

        virtual void updateBounds(double robot_x, double robot_y, double robot_yaw, double* min_x, double* min_y,
                                  double* max_x, double* max_y);
        virtual void updateCosts(costmap_2d::Costmap2D& master_grid, int min_i, int min_j, int max_i, int max_j);

        virtual void matchSize();

        // virtual void onFootprintChanged(); // not relative

        virtual void reset() {
            onInitialize();
        }

        virtual void deactivate() {
        } // stop

        virtual void activate() {
        } // start

        void setInflationParameters(double default_radius, double max_radius, bool use_dynamic_radius);

        void getInflationParameters(double& default_radius, double& max_radius) const;

    private:
        void pathCB(const nav_msgs::Path& path_msg);

        void reconfigureCB(MyConfig& config, uint32_t level);

        void computeCaches();

        unsigned int GetIndex(double radius);

        void addCircle(int ox, int oy, double radius);

        void findCountours();

        std::string global_frame_ = "map"; ///< @brief The global frame for the costmap
        std::string path_topic_ = "";

        bool use_dynamic_radius_ = false;
        double max_inflation_radius_ = 0;
        double default_inflation_radius_ = 0;
        unsigned int max_cell_inflation_radius_ = 0;
        unsigned int default_cell_inflation_radius_ = 0;
        std::shared_ptr<MyReconfigureServer> dsrv_;
        std::map<unsigned int, CirclePoints> map_radius_circles_;

        double expect_update_rate_ = -1.;
        ros::Time last_receive_stamp_ = ros::Time(0.);
        bool need_reinflation_ = false;
        bool path_received_ = false;
        ros::Subscriber path_sub_;
        nav_msgs::Path path_msg_;
        std::vector<MapLocation> boundry_cells_;
        int boundry_to_global_cell_dx_ = 0;
        int boundry_to_global_cell_dy_ = 0;
        boost::recursive_mutex inflation_access_;
        boost::recursive_mutex path_access_;
        double min_x_ = 0;
        double min_y_ = 0;
        double max_x_ = 0;
        double max_y_ = 0;

        cv::Mat mat_;
        std::vector<cv::Vec4i> hierarchy_;
        std::vector<std::vector<cv::Point>> contours_;
        const int pad_for_mat = 1;
    };

} // namespace costmap_2d

#endif // PATH_BOUNDRY_LAYER_H_

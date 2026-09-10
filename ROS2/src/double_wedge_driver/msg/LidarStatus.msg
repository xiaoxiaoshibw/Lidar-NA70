# LidarStatus.msg
std_msgs/Header header
bool connect_state       #通断：0-正常  1-异常
bool sync_time_state     #时间同步：0-正常  1-异常
bool motor_one_state     #1号电机状态：0-正常  1-异常
bool motor_two_state     #2号电机状态：0-正常  1-异常
bool encoder_one_state   #1号码盘状态：0-正常  1-异常
bool encoder_two_state   #2号码盘状态：0-正常  1-异常
bool sys_temp_state      #系统温度状态：0-正常  1-异常
bool rec_temp_state      #接收板温度状态：0-正常  1-异常
bool laser_temp_state    #激光器温度状态：0-正常  1-异常
bool laser_ray_state     #激光器出光状态：0-正常  1-异常
bool rec_bias_vol_state  #接收偏置电压状态：0-正常  1-异常

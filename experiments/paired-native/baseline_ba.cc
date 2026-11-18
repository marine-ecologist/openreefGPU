// Temporary OpenReef check: independent poses + shared fisheye intrinsics + distance factors.
// Projection follows COLMAP THIN_PRISM_FISHEYE with fx=fy and k4=sx=sy=0.
#include <ceres/ceres.h>
#include <ceres/rotation.h>
#include <array>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
using V3=std::array<double,3>; using Pose=std::array<double,6>; using Cal=std::array<double,8>;
struct Observation {int image,point; double x,y;};
struct Pair {int a,b; double length,sigma;};
template<class T> void center(const T* pose,T* c){T inverse[3]={-pose[0],-pose[1],-pose[2]};ceres::AngleAxisRotatePoint(inverse,pose+3,c);for(int k=0;k<3;++k)c[k]=-c[k];}
struct Projection {
 double x,y;
 template<class T> bool operator()(const T* pose,const T* cal,const T* point,T* r)const{
  T v[3];ceres::AngleAxisRotatePoint(pose,point,v);for(int k=0;k<3;++k)v[k]+=pose[k+3];
  const T rho=sqrt(v[0]*v[0]+v[1]*v[1]+T(1e-24));
  const T theta=atan2(rho,v[2]),u=theta*v[0]/rho,w=theta*v[1]/rho;
  const T rr=u*u+w*w,rad=T(1)+cal[3]*rr+cal[4]*rr*rr+cal[5]*rr*rr*rr;
  const T dx=u*rad+T(2)*cal[6]*u*w+cal[7]*(rr+T(2)*u*u);
  const T dy=w*rad+cal[6]*(rr+T(2)*w*w)+T(2)*cal[7]*u*w;
  r[0]=cal[0]*dx+cal[1]-T(x);r[1]=cal[0]*dy+cal[2]-T(y);return true;
 }
};
struct Distance {
 double length,sigma;
 template<class T> bool operator()(const T* a,const T* b,T* r)const{
  T ca[3],cb[3];center(a,ca);center(b,cb);T d=T(0);for(int k=0;k<3;++k)d+=(ca[k]-cb[k])*(ca[k]-cb[k]);
  r[0]=(sqrt(d+T(1e-24))-T(length))/T(sigma);return true;
 }
};
int main(int argc,char** argv){try{
 if(argc<4)throw std::runtime_error("usage: paired-baseline-ba input.tsv output.tsv iterations [--gpu] [--no-bars]");
 bool gpu=false,bars=true;for(int i=4;i<argc;++i){std::string a=argv[i];if(a=="--gpu")gpu=true;else if(a=="--no-bars")bars=false;else throw std::runtime_error("unknown option");}
 std::map<int,Pose> poses;std::map<int,Cal> cameras;std::map<int,V3> points;std::map<int,int> camera_of;
 std::vector<Observation> observations;std::vector<Pair> pairs;std::ifstream input(argv[1]);if(!input)throw std::runtime_error("cannot open input");
 std::string line;while(std::getline(input,line)){if(line.empty()||line[0]=='#')continue;std::istringstream s(line);char type;int id;s>>type;
  if(type=='c'){s>>id;Cal v;for(double& x:v)s>>x;cameras[id]=v;}
  else if(type=='i'){s>>id>>camera_of[id];Pose v;for(double& x:v)s>>x;poses[id]=v;}
  else if(type=='p'){s>>id;V3 v;for(double& x:v)s>>x;points[id]=v;}
  else if(type=='o'){Observation o;s>>o.image>>o.point>>o.x>>o.y;observations.push_back(o);}
  else if(type=='b'){Pair p;s>>p.a>>p.b>>p.length>>p.sigma;if(p.length<=0||p.sigma<=0)throw std::runtime_error("invalid distance factor");pairs.push_back(p);}
  else throw std::runtime_error("unknown input record");if(!s)throw std::runtime_error("invalid input record");
 }
 if(poses.size()<3||cameras.empty()||points.empty()||observations.empty()||pairs.empty())throw std::runtime_error("insufficient geometry or paired cameras");
 ceres::Problem problem;auto order=std::make_shared<ceres::ParameterBlockOrdering>();
 for(auto& [id,p]:points){problem.AddParameterBlock(p.data(),3);order->AddElementToGroup(p.data(),0);}
 for(auto& [id,p]:poses){problem.AddParameterBlock(p.data(),6);order->AddElementToGroup(p.data(),1);}
 for(auto& [id,c]:cameras){problem.AddParameterBlock(c.data(),8);problem.SetParameterLowerBound(c.data(),0,1.0);order->AddElementToGroup(c.data(),1);}
 for(const auto& o:observations){auto& pose=poses.at(o.image);auto& cal=cameras.at(camera_of.at(o.image));auto& point=points.at(o.point);
  problem.AddResidualBlock(new ceres::AutoDiffCostFunction<Projection,2,6,8,3>(new Projection{o.x,o.y}),new ceres::HuberLoss(1.0),pose.data(),cal.data(),point.data());}
 // Fix only the global coordinate gauge, never the A/B relative orientation.
 problem.SetParameterBlockConstant(poses.begin()->second.data());
 if(bars){for(const auto& p:pairs)problem.AddResidualBlock(new ceres::AutoDiffCostFunction<Distance,1,6,6>(new Distance{p.length,p.sigma}),nullptr,poses.at(p.a).data(),poses.at(p.b).data());}
 else { // One gauge-only distance fixes global scale; all remaining pair distances stay unconstrained.
  const auto& p=pairs.front();double a[3],b[3];center(poses.at(p.a).data(),a);center(poses.at(p.b).data(),b);double d=0;for(int k=0;k<3;++k)d+=(a[k]-b[k])*(a[k]-b[k]);
  problem.AddResidualBlock(new ceres::AutoDiffCostFunction<Distance,1,6,6>(new Distance{sqrt(d),1e-6}),nullptr,poses.at(p.a).data(),poses.at(p.b).data());
 }
 ceres::Solver::Options options;options.linear_solver_type=ceres::SPARSE_SCHUR;options.linear_solver_ordering=order;options.max_num_iterations=std::stoi(argv[3]);
 options.num_threads=8;options.minimizer_progress_to_stdout=true;options.function_tolerance=1e-8;
 if(gpu){if(!ceres::IsSparseLinearAlgebraLibraryTypeAvailable(ceres::CUDA_SPARSE))throw std::runtime_error("Ceres CUDA sparse backend unavailable; use a CUDA-enabled worker or omit --gpu");options.sparse_linear_algebra_library_type=ceres::CUDA_SPARSE;}
 ceres::Solver::Summary summary;ceres::Solve(options,&problem,&summary);std::cout<<summary.FullReport()<<std::endl;
 if(!summary.IsSolutionUsable()||summary.final_cost>summary.initial_cost)throw std::runtime_error("bundle adjustment failed to improve a usable solution");
 std::ofstream out(argv[2]);out<<std::setprecision(17);for(const auto& [id,c]:cameras){out<<"c "<<id;for(double x:c)out<<" "<<x;out<<"\n";}
 for(const auto& [id,p]:poses){out<<"i "<<id;for(double x:p)out<<" "<<x;out<<"\n";}for(const auto& [id,p]:points){out<<"p "<<id;for(double x:p)out<<" "<<x;out<<"\n";}
 out<<"s "<<summary.initial_cost<<" "<<summary.final_cost<<" "<<summary.num_residuals<<"\n";if(!out)throw std::runtime_error("could not save solution");return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}}

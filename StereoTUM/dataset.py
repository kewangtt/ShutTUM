import yaml
import cv2
import os.path as p
import numpy   as np

from collections import namedtuple
import StereoTUM.devices
import StereoTUM.values


class Dataset(object):
    r"""The base class representing one dataset record.
    
    In order for performent operations the dataset loads and checks lots of the data in its :any:`__init__` thus avoiding
    expensive checks in for loops or list comprehensions. In addition it holds the reference to list-like objects, such 
    as :any:`cameras` or :any:`imu <StereoTUM.Dataset.imu>`. You can iterate over each of these (depending
    on application) to get the corresponding :any:`Value` s in order.
    
    A typical application might look like::
    
        # Load a dataset
        dataset = Dataset('path/to/folder/0001')
        
        # Iterate over all imu values and find corresponding images
        for observation in dataset.imu:
            
            print(observation.acceleration)
            print(observation.angular_velocity)
            
            stereo = observation.stereo(shutter='global')
            if stereo is None: continue
            
            print(stereo.L.ID)
    
    """

    _Data = namedtuple('Data', 'global_ rolling imu groundtruth stamp')

    @staticmethod
    def _check_folder_exists(folder):
        r""" Checks if a folder exists and raises an exception otherwise
        :param str folder: the path to the folder to check for existance
        :raises IOError
        """
        if not p.exists(folder):
            raise IOError("Could not find folder %s, record folder seems not to be valid!" % folder)

    @staticmethod
    def _check_file_exists(file):
        r""" Checks if a file exists and raises an exception otherwise
        :param str file: the path to the file to check for existance
        :raises IOError
        """
        if not p.exists(file):
            raise IOError("Could not find %s, record folder seems not to be valid!" % file)

    @staticmethod
    def _check_contains_key(file, dictionary, key):
        r"""
        :param str file: The reference file, which to give in the error message
        :param dict dictionary: the dict to check the key's existance
        :param str key: the key to check for 
        :raises ValueError
        
        Checks if a dict originating from a certain file contains a certain key and raises an exception otherwise
        """
        if key not in dictionary:
            raise ValueError("Could not find %s in %s, record folder seems not to be valid!" % (key, file))

    def __init__(self, path):
        r""" 
        :param str path: the path to *one* record of the dataset, such as ``~/StereoTUM/0001``
        :raises: ValueError: if anything goes wrong 
        Load the dataset into memory (except images) and do basic data consistency checks.
        
        1. It is checked that in path there exists a ``data``, ``frames`` and ``params`` folder
        2. It is checked that there exists the files ``data/frames.csv``, ``data/imu.csv`` and ``data/ground_truth.csv``
        3. The files from 2. are loaded into memory (see :any:`raw`)
        4. The ``params/time.yaml`` is loaded
        5. The ``params/params.yaml`` is loaded
           
        """
        path = p.expandvars(p.expanduser(path))
        self._path = path

        # Consistency Check
        Dataset._check_folder_exists(p.join(path, 'data'))
        Dataset._check_folder_exists(p.join(path, 'frames'))
        Dataset._check_folder_exists(p.join(path, 'params'))

        f = p.join(path, 'data', 'frames.csv')
        i = p.join(path, 'data', 'imu.csv')
        g = p.join(path, 'data', 'ground_truth.csv')

        Dataset._check_file_exists(f)
        Dataset._check_file_exists(i)
        Dataset._check_file_exists(g)

        self._frames       = np.genfromtxt(f, delimiter='\t', skip_header=1)
        self._imu          = np.genfromtxt(i, delimiter='\t', skip_header=1)
        self._ground_truth = np.genfromtxt(g, delimiter='\t', skip_header=1)
        self._times = list(sorted(set(self._frames[:,0]) | set(self._imu[:,0]) | set(self._ground_truth[:,0])))

        Raw = namedtuple('Raw', ['frames', 'imu', 'groundtruth'])
        self._raw = Raw(self._frames, self._imu, self._ground_truth)
        self._time = {}
        timefile = p.join(path, 'params', 'time.yaml')
        Dataset._check_file_exists(timefile)
        with open(timefile) as stream:
            self._time = yaml.load(stream)['time']
            Dataset._check_contains_key(timefile, self._time, 'start')
            Dataset._check_contains_key(timefile, self._time, 'end')
            Dataset._check_contains_key(timefile, self._time, 'duration')

        self._cams = {}
        paramfile = p.join(path, 'params', 'params.yaml')
        Dataset._check_file_exists(paramfile)
        with open(paramfile) as stream:
            self._refs = yaml.load(stream)

        self._gammas = {}
        self._cams = {}
        for ref in self._refs:
            if ref == 'world': continue  # world param must only be present, not more

            # Every other reference must have at least a transform parameter
            Dataset._check_contains_key(paramfile, self._refs[ref], 'transform')

            if 'shutter' not in self._refs[ref]: continue

            # If the reference contains a 'shutter' param, it is a camera
            cam = ref
            self._cams[cam] = self._refs[ref]

            folder   = p.join(path,   'params', cam)
            gamma    = p.join(folder, 'gamma.txt')
            vignette = p.join(folder, 'vignette.png')
            Dataset._check_folder_exists(folder)
            Dataset._check_file_exists(gamma)
            Dataset._check_file_exists(vignette)
            self._gammas[cam] = np.genfromtxt(gamma, delimiter=' ')

        self._cameras = StereoTUM.devices.DuoStereoCamera(self)
        self._imu     = StereoTUM.devices.Imu(self)
        self._mocap   = StereoTUM.devices.Mocap(self)

    @property
    def raw(self):
        r"""
        The raw values in matrix form. This property is a Named Tuple with the following fields:
        
        * ``frames`` (`ndarray <https://docs.scipy.org/doc/numpy-1.13.0/reference/generated/numpy.ndarray.html>`_) corresponding to ``data/frames.csv``
        * ``imu`` (`ndarray <https://docs.scipy.org/doc/numpy-1.13.0/reference/generated/numpy.ndarray.html>`_) corresponding to ``data/imu.csv``
        * ``groundtruth`` (`ndarray <https://docs.scipy.org/doc/numpy-1.13.0/reference/generated/numpy.ndarray.html>`_) corresponding to ``data/ground_truth.csv``
        
        """
        return self._raw

    def cameras(self, shutter='both'):
        r""" 
        :param str shutter: {both/global/rolling} the type of shutter you are interested in. 
        :return The reference of the cameras, which you can iterate either as :any:`StereoCamera` (global/rolling)or :any:`DuoStereoCamera` (both)
        :raise: ValueError: for anything other then both/global/rolling
        
        Get a reference to one or both of the two stereo cameras, to iterate over their images
        
        Note that often you want to directly iterate over those references, which you can do like::
        
            # Iterate over both rolling and global images
            for g, r in dataset.cameras(shutter='both'):
                print(g.L.ID)
                print(r.R.stamp)
        
        """
        if shutter == 'both': return self._cameras
        if shutter == 'global': return self._cameras._global
        if shutter == 'rolling': return self._cameras._rolling

        raise ValueError('Unknown shutter type: use either "global", "rolling", or "both" and not %s' % shutter)

    @property
    def imu(self):
        r""" 
        :return: The reference of the Imu for this dataset, which you can iterate as :any:`Imu`
        
        Get a reference to the IMU to iterate over its values.
        
        Note that often you want to directly iterate over the Imu like so::
            
            # Iterate over all imu values
            for observation in dataset.imu:
                print(observation.acceleration)
            
        
        """
        return self._imu

    @property
    def mocap(self):
        r"""
        :return: The reference to the Ground Truth list as :any:`Mocap`
       
        Get a reference to the Motion Capture system to iterate over the ground truth values.
         
        Note that often you want to iterate directly over the Ground Truth values like so::
            
            # Iterate over all ground truth values
            for gt in dataset.mocap:
                print(gt.stamp)
                print(gt.position)
            
        
        """
        return self._mocap

    @property
    def times(self):
        r"""
        A list of all time stamps in the dataset.
        
        .. math:: \mathbf{t} = \mathbf{t}_{frames} \cup \mathbf{t}_{imu} \cup \mathbf{t}_{groundtruth}
        
        Note that this list is sorted, so you can easily iterate over it like so::
        
            for time in dataset.times:
                print(time)
        
        
        """
        return self._times

    @property
    def start(self):
        r"""
        The start time of the record as Unix Timestamp in seconds with decimal places
        """
        return self._time['start']

    @property
    def end(self):
        r"""
        The end time of the record as Unix Timestamp in seconds with decimal places
        """
        return self._time['end']

    @property
    def duration(self):
        r""" 
        The duration of the record in seconds as float, so basically:
        ``dataset.end - dataset.start``
        
        """
        return self._time['duration']

    @property
    def resolution(self):
        r""" Returns the resolution of the cameras as a named tuple ``(width, height)`` """
        Resolution = namedtuple('Resolution', 'width height')
        return Resolution(width=1280, height=1024)


    @property
    def exposure_limits(self):
        r"""
        :return: a Namped Tuple with the fields ``min`` and ``max`` which each contain a float indicating the minimum
        and maximum exposure time in milliseconds
        
        The minimal & maximal exposure used for all cameras. Note that these values are the *limits*
        not the extrema of the record, so most of the time, these will not be reached, but if, clamped accordingly.::
        
            limits = dataset.exposure_limits
            print("Limits are %s .. %s ms" % limits.min, limits.max)
            
        
        """
        Limits = namedtuple('Limits', ['min', 'max'])
        for cam in self._cams:
            # take the first camera, since all limits are the same
            exp = self._cams[cam]['exposure']
            return Limits(min=exp['min'], max=exp['max'])

    def gamma(self, cam, input):
        r""" 
        :param str cam: the name of the camera (e.g. ``"cam1"``)
        :param float input: the position to lookup, i.e. X-axis on luminance plot. Between 0 .. 255, will be rounded to int
        :raises: ValueError: for unknown camera names or inputs below 0 or above 255
        
        Lookup a gamma value from ``params/<cam>/gamma.txt``
        """
        if cam not in self._cams:
            raise ValueError("Unknown camera name: %s" % cam)
        if input < 0 or input > 255:
            raise ValueError("Gamma function only defined for inputs from 0 .. 255 and not for %s" % input)
        return self._gammas[cam][round(input)]

    def vignette(self, cam):
        r"""
        :param str cam: the name of the camera to lookup its vignette (e.g. ``"cam1"``)  
        :return: the vignette image, read by `cv2.imread() <http://docs.opencv.org/3.0-beta/doc/py_tutorials/py_gui/py_image_display/py_image_display.html>`_ with dimensions [1280x1024] as grayscale
        :rtype: `ndarray <https://docs.scipy.org/doc/numpy-1.13.0/reference/generated/numpy.ndarray.html>`_
        """
        if cam not in self._cams:
            raise ValueError("Unknown camera name: %s" % cam)
        file = p.join(self._path, 'params', cam, 'vignette.png')
        return cv2.imread(file, cv2.IMREAD_GRAYSCALE)

    @property
    def rolling_shutter_speed(self):
        r"""
        How fast did the two rolling shutter cameras shuttered. Returns the time between the exposure of two consecutive 
        rows in milli seconds (approximate)
        
        """
        for cam in self._cams:
            shutter = self._cams[cam]['shutter']
            if shutter['type'] == 'rolling':
                return shutter['speed']
        raise ValueError('No cams in %s had rolling shutter enabled!' % self._path)

    def _find_data_for(self, s):
        value = StereoTUM.values.Value(self, s, 'world')  # world as dummy for the time stamp
        return Dataset._Data(
            stamp=s,
            global_=StereoTUM.values.StereoImage.extrapolate(value, 'global', method='exact'),
            rolling=StereoTUM.values.StereoImage.extrapolate(value, 'rolling', method='exact'),
            imu=StereoTUM.values.ImuValue.extrapolate(value, method='exact'),
            groundtruth=StereoTUM.values.GroundTruth.extrapolate(value, method='exact')
        )

    def _find_data_between(self, start, stop):
        for time in self._times:
            if start is not None and time < start: continue
            if stop  is not None and time > stop:  continue
            yield self._find_data_for(time)

    def __getitem__(self, stamp):
        r"""
        :param float/slice stamp: either the time at which to look up the values or a slice object (e.g. start:stop) 
                                  defining a range of time stamps, between which to lookup the values. If the start 
                                  value of the slice is before :any:`start`, or the end value of the slice is after 
                                  :any:`end`, then the generator yield up to :any:`start` or :any:`end`, respectively.
                                  Note that times for the slice are both *inclusive* unlike normal python index slices.
        :return: either a named tuple with the fields 
                  * ``stamp`` (float)
                  * ``global_`` (:any:`StereoImage`) 
                  * ``rolling`` (:any:`StereoImage`) 
                  * ``imu`` (:any:`ImuValue`) and 
                  * ``groundtruth`` (:any:`GroundTruth`). 
                 
                 or a generator yielding multiple (or none) of these. If any of the values of the above tuple does not 
                 exist for the stamp, it becomes ``None``. Note the spelling of ``global_``, since ``global`` 
                 is a reserved keyword in python.
        
        Looks up all corresponding :any:`Value` s it can find for a given time stamp::
        
            dataset = Dataset(...)
            
            # The single lookup with one float as index
            for time in dataset.times:
                data = dataset[time]
                if data.global_     is not None: print(data.global_.ID)
                if data.rolling     is not None: print(data.rolling.ID)
                if data.imu         is not None: print(data.imu.acceleration)
                if data.groundtruth is not None: print(data.groundtruth.pose)
        
            # ... or the sliced version specifying all data between 5s .. 45s
            for data in dataset[5:45]:
               print(data)
                
            # ... or all up to 10s
            for beginning in dataset[:10]
                print(data)
                
            # ... or all from 30s till end
            for finish in dataset[30:]
                print(data)
                
            # Custom steps, however, are not supported:
            try:
                x = dataset[::-1]
            except ValueError:
                print("Doesn't make sense...")
        
                
            
        Note, though, that this is not the most performant thing to iterate over the dataset,
        since the lookup has to be done on every iteration. Consider iterating over :any:`cameras`, :any:`imu` or 
        :any:`mocap` instead.
        """
        if isinstance(stamp, slice):
            if stamp.step is not None:
                raise ValueError('Slicing a dataset with a step value like %s:%s:%s is not supported'
                                 % (stamp.start, stamp.stop, stamp.step))
            return self._find_data_between(stamp.start, stamp.stop)
        else:
            return self._find_data_for(stamp)

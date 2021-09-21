# -*- coding: utf-8 -*-
#
# This file is part of the pyFDA project hosted at https://github.com/chipmuenk/pyfda
#
# Copyright © pyFDA Project Contributors
# Licensed under the terms of the MIT License
# (see file LICENSE in root directory for details)

"""
Widget for plotting impulse and general transient responses
"""
# import time
from pyfda.libs.compat import QWidget, pyqtSignal, QTabWidget, QVBoxLayout
import numpy as np
from numpy import ndarray, pi
import scipy.signal as sig
from scipy.special import sinc, diric

import pyfda.filterbroker as fb
import pyfda.libs.pyfda_fix_lib as fx
from pyfda.libs.pyfda_sig_lib import angle_zero
from pyfda.libs.pyfda_lib import (
    safe_eval, pprint_log, np_type, calc_ssb_spectrum,
    rect_bl, sawtooth_bl, triang_bl, comb_bl, calc_Hcomplex, safe_numexpr_eval)

from pyfda.pyfda_rc import params  # FMT string for QLineEdit fields, e.g. '{:.3g}'
from pyfda.plot_widgets.tran.plot_tran_stim_ui import Plot_Tran_Stim_UI

import logging
logger = logging.getLogger(__name__)


class Plot_Tran_Stim(QWidget):
    """
    Construct a widget for plotting impulse and general transient responses
    """
    sig_rx = pyqtSignal(object)  # incoming
    sig_tx = pyqtSignal(object)  # outgoing, e.g. when stimulus has been calculated
    from pyfda.libs.pyfda_qt_lib import emit

    def __init__(self):
        super().__init__()
        self.ui = Plot_Tran_Stim_UI(self)  # create the UI part with buttons etc.

        # initial settings
        self.needs_calc = True   # flag whether plots need to be recalculated
        self.needs_redraw = [True] * 2  # flag which plot needs to be redrawn
        self.error = False
        
        self._construct_UI()

# ------------------------------------------------------------------------------
    def process_sig_rx(self, dict_sig=None) -> None:
        """
        Process signals coming from
        - the navigation toolbars (time and freq.)
        - local widgets (impz_ui) and
        - plot_tab_widgets() (global signals)
        """

        logger.warning("SIG_RX - needs_calc: {0} | vis: {1}\n{2}"
                       .format(self.needs_calc, self.isVisible(), pprint_log(dict_sig)))

    def _construct_UI(self) -> None:
        """
        Instantiate the UI of the widget.
        """
        self.main_wdg = QWidget()
        layVMain = QVBoxLayout()
        layVMain.addWidget(self.ui.wdg_stim)
        layVMain.setContentsMargins(*params['mpl_margins'])

        self.ui.sig_tx.connect(self.sig_tx)  # relay UI events further up
        self.sig_rx.connect(self.ui.sig_rx)  # ... and the other way round

        self.setLayout(layVMain)

# ------------------------------------------------------------------------------
    def calc_stimulus_frame(self, N_first: int, N: int, N_end: int, 
                            init: bool = False) -> ndarray:
        """
        Calculate a data frame of stimulus `x` with a length of `N` samples,
        starting with index `N_first`

        Parameters
        ----------
        N_first: int
            index of first data point

        N: int
            number of samples to be generated
            
        init: bool
            when init == True, initialize stimulus settings

        Returns
        -------
        x: ndarray
            an array with `N` stimulus data points
        """
        if init or N_first == 0:
            '''intialize title string, y-axis label and some variables'''
            # use radians for angle internally
            self.rad_phi1 = self.ui.phi1 / 180 * pi
            self.rad_phi2 = self.ui.phi2 / 180 * pi
            if (self.ui.ledDC.isVisible and type(self.ui.DC) == complex) or\
                (self.ui.ledAmp1.isVisible and type(self.ui.A1) == complex) or\
                    (self.ui.ledAmp2.isVisible and type(self.ui.A2) == complex): 
                self.xf = np.zeros(N, dtype=complex)
            else:
                self.xf = np.zeros(N, dtype=float)

            self.H_str = r'$y[n]$'  # default
            self.title_str = ""
            if self.ui.stim == "none":
                self.title_str = r'Zero Input System Response'
                self.H_str = r'$h_0[n]$' 
            # ------------------------------------------------------------------
            elif self.ui.stim == "dirac":
                self.title_str = r'Impulse Response'
                self.H_str = r'$h[n]$'
            elif self.ui.stim == "sinc":
                self.title_str = r'Sinc Impulse '
            elif self.ui.stim == "gauss":
                self.title_str = r'Gaussian Impulse '
            elif self.ui.stim == "rect":
                self.title_str = r'Rect Impulse '
            # ------------------------------------------------------------------
            elif self.ui.stim == "step":
                if self.ui.chk_step_err.isChecked():
                    self.title_str = r'Settling Error $\epsilon$'
                    self.H_str = r'$h_{\epsilon, \infty} - h_{\epsilon}[n]$'
                else:
                    self.title_str = r'Step Response'
                    self.H_str = r'$h_{\epsilon}[n]$'
            # ------------------------------------------------------------------
            elif self.ui.stim == "cos":
                self.title_str = r'Cosine Stimulus'
            elif self.ui.stim == "sine":
                self.title_str = r'Sinusoidal Stimulus '
            elif self.ui.stim == "exp":
                self.title_str = r'Complex Exponential Stimulus '
            elif self.ui.stim == "diric":
                self.title_str = r'Periodic Sinc Stimulus'
            # ------------------------------------------------------------------
            elif self.ui.stim == "chirp":
                self.title_str = self.ui.chirp_type.capitalize() + ' Chirp Stimulus'
            # ------------------------------------------------------------------
            elif self.ui.stim == "triang":
                if self.ui.but_stim_bl.isChecked():
                    self.title_str = r'Bandlim. Triangular Stimulus'
                else:
                    self.title_str = r'Triangular Stimulus'
            elif self.ui.stim == "saw":
                if self.ui.but_stim_bl.isChecked():
                    self.title_str = r'Bandlim. Sawtooth Stimulus'
                else:
                    self.title_str = r'Sawtooth Stimulus'
            elif self.ui.stim == "square":
                if self.ui.but_stim_bl.isChecked():
                    self.title_str = r'Bandlimited Rect. Stimulus'
                else:
                    self.title_str = r'Rect. Stimulus'
            elif self.ui.stim == "comb":
                self.title_str = r'Bandlim. Comb Stimulus'
            # ------------------------------------------------------------------
            elif self.ui.stim == "am":
                self.title_str = (
                    r'AM Stimulus: $A_1 \sin(2 \pi n f_1 + \varphi_1)'
                    r'\cdot A_2 \sin(2 \pi n f_2 + \varphi_2)$')
            elif self.ui.stim == "pmfm":
                self.title_str = (
                    r'PM / FM Stimulus: $A_1 \sin(2 \pi n f_1'
                    r'+ \varphi_1 + A_2 \sin(2 \pi n f_2 + \varphi_2))$')
            # ------------------------------------------------------------------
            elif self.ui.stim == "formula":
                self.title_str = r'Formula Defined Stimulus'
            # ==================================================================
            if self.ui.noise == "gauss":
                self.title_str += r' + Gaussian Noise'
            elif self.ui.noise == "uniform":
                self.title_str += r' + Uniform Noise'
            elif self.ui.noise == "prbs":
                self.title_str += r' + PRBS Noise'
            elif self.ui.noise == "mls":
                self.title_str += r' + max. length sequence'
            elif self.ui.noise == "brownian":
                self.title_str += r' + Brownian Noise'
            # ==================================================================
            if self.ui.ledDC.isVisible and self.ui.DC != 0:
                self.title_str += r' + DC'
        # ----------------------------------------------------------------------

        N_last = N_first + N
        n = np.arange(N_first, N_last)

        # T_S = fb.fil[0]['T_S']
        T1_int = int(np.round(self.ui.T1))

        # calculate stimuli x[n] ==============================================
        if self.ui.stim == "none":
            self.xf.fill(0)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "dirac":
            self.xf.fill(0)  # = np.zeros(N, dtype=A_type)
            if N_first <= T1_int < N_last:
                self.xf[T1_int - N_first] = self.ui.A1  # create dirac impulse as input signal
        # ----------------------------------------------------------------------
        elif self.ui.stim == "sinc":
            self.xf = self.ui.A1 * sinc(2 * (n - self.ui.T1) * self.ui.f1)\
                + self.ui.A2 * sinc(2 * (n - self.ui.T2) * self.ui.f2)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "gauss":
            self.xf = self.ui.A1 * sig.gausspulse(
                    (n - self.ui.T1), fc=self.ui.f1, bw=self.ui.BW1) +\
                self.ui.A2 * sig.gausspulse(
                    (n - self.ui.T2), fc=self.ui.f2, bw=self.ui.BW2)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "rect":
            n_rise = int(T1_int - np.floor(self.ui.TW1/2))
            n_min = max(n_rise, 0)
            n_max = min(n_rise + self.ui.TW1, N)
            self.xf = self.ui.A1 * np.where((n >= n_min) & (n < n_max), 1, 0)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "step":
            if T1_int < N_first:   # step before current frame
                self.xf.fill(self.ui.A1)
            if N_first <= T1_int < N_last:  # step in current frame
                self.xf[0:T1_int - N_first].fill(0)
                self.xf[T1_int - N_first:].fill(self.ui.A1)
            elif T1_int >= N_last:  # step after current frame
                self.xf.fill(0)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "cos":
            self.xf =\
                self.ui.A1 * np.cos(2*pi * n * self.ui.f1 + self.rad_phi1) +\
                self.ui.A2 * np.cos(2*pi * n * self.ui.f2 + self.rad_phi2)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "sine":
            self.xf =\
                self.ui.A1 * np.sin(2*pi * n * self.ui.f1 + self.rad_phi1) +\
                self.ui.A2 * np.sin(2*pi * n * self.ui.f2 + self.rad_phi2)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "exp":
            self.xf =\
                self.ui.A1 * np.exp(1j * (2 * pi * n * self.ui.f1 + self.rad_phi1)) +\
                self.ui.A2 * np.exp(1j * (2 * pi * n * self.ui.f2 + self.rad_phi2))
        # ----------------------------------------------------------------------
        elif self.ui.stim == "diric":
            self.xf = self.ui.A1 * diric(
                (4 * pi * (n-self.ui.T1) * self.ui.f1 + self.rad_phi1*2)
                / self.ui.TW1, self.ui.TW1)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "chirp":
            if self.ui.T2 == 0:  # sig.chirp is buggy, T_sim cannot be larger than T_end
                T_end = N_end  # frequency sweep over complete interval
            else:
                T_end = self.ui.T2  # frequency sweep till T2
            self.xf = self.ui.A1 * sig.chirp(
                n, self.ui.f1, T_end, self.ui.f2,
                method=self.ui.chirp_type, phi=self.rad_phi1)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "triang":
            if self.ui.but_stim_bl.isChecked():
                self.xf = self.ui.A1 * triang_bl(2*pi * n * self.ui.f1 + self.rad_phi1)
            else:
                self.xf = self.ui.A1 * sig.sawtooth(
                    2*pi * n * self.ui.f1 + self.rad_phi1, width=0.5)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "saw":
            if self.ui.but_stim_bl.isChecked():
                self.xf = self.ui.A1 * sawtooth_bl(2*pi * n * self.ui.f1 + self.rad_phi1)
            else:
                self.xf = self.ui.A1 * sig.sawtooth(2*pi * n * self.ui.f1 + self.rad_phi1)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "square":
            if self.ui.but_stim_bl.isChecked():
                self.xf = self.ui.A1 * rect_bl(
                    2 * pi * n * self.ui.f1 + self.rad_phi1, duty=self.ui.stim_par1)
            else:
                self.xf = self.ui.A1 * sig.square(
                    2 * pi * n * self.ui.f1 + self.rad_phi1, duty=self.ui.stim_par1)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "comb":
            self.xf = self.ui.A1 * comb_bl(2 * pi * n * self.ui.f1 + self.rad_phi1)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "am":
            self.xf = self.ui.A1 * np.sin(2*pi * n * self.ui.f1 + self.rad_phi1)\
                * self.ui.A2 * np.sin(2*pi * n * self.ui.f2 + self.rad_phi2)
        # ----------------------------------------------------------------------
        elif self.ui.stim == "pmfm":
            self.xf = self.ui.A1 * np.sin(
                2 * pi * n * self.ui.f1 + self.rad_phi1 +
                self.ui.A2 * np.sin(2*pi * n * self.ui.f2 + self.rad_phi2))
        # ----------------------------------------------------------------------
        elif self.ui.stim == "formula":
            param_dict = {"A1": self.ui.A1, "A2": self.ui.A2,
                          "f1": self.ui.f1, "f2": self.ui.f2,
                          "phi1": self.ui.phi1, "phi2": self.ui.phi2,
                          "BW1": self.ui.BW1, "BW2": self.ui.BW2,
                          "f_S": fb.fil[0]['f_S'], "n": n}

            self.xf = safe_numexpr_eval(self.ui.stim_formula, (N,), param_dict)
        else:
            logger.error('Unknown stimulus format "{0}"'.format(self.ui.stim))
            return None
        # ----------------------------------------------------------------------
        # Add noise to stimulus
        noi = 0
        if self.ui.noise == "none":
            pass
        elif self.ui.noise == "gauss":
            noi = self.ui.noi * np.random.randn(N)
        elif self.ui.noise == "uniform":
            noi = self.ui.noi * (np.random.rand(N)-0.5)
        elif self.ui.noise == "prbs":
            noi = self.ui.noi * 2 * (np.random.randint(0, 2, N)-0.5)
        elif self.ui.noise == "mls":
            # max_len_seq returns `sequence, state`. The state is not stored here,
            # hence, an identical sequence is created every time.
            noi = self.ui.noi * 2 * (sig.max_len_seq(int(np.ceil(np.log2(N))),
                                     length=N, state=None)[0] - 0.5)
        elif self.ui.noise == "brownian":
            # brownian noise
            noi = np.cumsum(self.ui.noi * np.random.randn(N))
        else:
            logger.error('Unknown kind of noise "{}"'.format(self.ui.noise))
        if type(self.ui.noi) == complex:
            self.xf = self.xf.astype(complex) + noi
        else:
            self.xf += noi
        # Add DC to stimulus when visible / enabled
        if self.ui.ledDC.isVisible:
            if type(self.ui.DC) == complex:
                self.xf = self.xf.astype(complex) + self.ui.DC
            else:
                self.xf += self.ui.DC
        logger.warning(type(self.xf[0]))

        return self.xf[:N]
# ------------------------------------------------------------------------------


if __name__ == "__main__":
    """ Run widget standalone with `python -m pyfda.plot_widgets.tran.plot_tran_stim` """
    import sys
    from pyfda.libs.compat import QApplication
    from pyfda import pyfda_rc as rc

    app = QApplication(sys.argv)
    app.setStyleSheet(rc.qss_rc)
    mainw = Plot_Tran_Stim()
    app.setActiveWindow(mainw)
    mainw.show()
    sys.exit(app.exec_())
